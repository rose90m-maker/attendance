"""
MES (제조실행시스템) Blueprint
================================
- ESP32 WiFi → POST /mes/count  (인증 토큰)
- 실시간 카운트 대시보드: /mes/realtime
- 일별/월별 리포트:        /mes/report
- 장치 관리:               /mes/devices  (admin)

app_maria.py에서:
    app.config['MARIA'] = MARIA
    app.config['MES_TOKEN'] = os.environ.get('MES_TOKEN', '')
    from mes_bp import mes_bp, init_mes_db
    init_mes_db(app)
    app.register_blueprint(mes_bp)
"""
import os
import json
import fcntl
import threading
from datetime import datetime, timedelta, date
from functools import wraps

import pymysql
import paho.mqtt.client as mqtt
from flask import (
    Blueprint, current_app, request, jsonify,
    render_template, redirect, url_for, session, flash,
)

mes_bp = Blueprint("mes", __name__, url_prefix="/mes")
_mqtt_app = None  # init_mes_db()에서 주입
_mqtt_lock_fd = None  # 구독자 단일화용 파일 락 (열린 상태 유지=락 보유)


# ── DB 헬퍼 ──────────────────────────────────────────────
def _conn():
    return pymysql.connect(**current_app.config["MARIA"])


# ── DB 초기화 (앱 시작 시 1회) ───────────────────────────
MES_INIT_SQL = [
    """CREATE TABLE IF NOT EXISTS mes_env_log (
        id            BIGINT AUTO_INCREMENT PRIMARY KEY,
        device_id     VARCHAR(40)  NOT NULL,
        temperature   FLOAT        NOT NULL,
        humidity      FLOAT        NOT NULL,
        recorded_at   DATETIME     NOT NULL DEFAULT NOW(),
        INDEX idx_env_device_time (device_id, recorded_at),
        INDEX idx_env_time (recorded_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS mes_devices (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        device_id   VARCHAR(40)  NOT NULL,
        name        VARCHAR(80)  NOT NULL DEFAULT '',
        line_name   VARCHAR(80)  NOT NULL DEFAULT '',
        target_day  INT          NOT NULL DEFAULT 2000,
        is_active   TINYINT(1)   NOT NULL DEFAULT 1,
        created_at  DATETIME     NOT NULL DEFAULT NOW(),
        UNIQUE KEY uq_device (device_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS mes_label_count (
        id          BIGINT AUTO_INCREMENT PRIMARY KEY,
        device_id   VARCHAR(40)  NOT NULL,
        count       INT          NOT NULL DEFAULT 0,
        recorded_at DATETIME     NOT NULL DEFAULT NOW(),
        INDEX idx_device_time (device_id, recorded_at),
        INDEX idx_time (recorded_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS mes_daily_summary (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        device_id   VARCHAR(40)  NOT NULL,
        work_date   DATE         NOT NULL,
        total_count INT          NOT NULL DEFAULT 0,
        peak_count  INT          NOT NULL DEFAULT 0,
        peak_hour   TINYINT,
        target      INT          DEFAULT NULL,
        updated_at  DATETIME     NOT NULL DEFAULT NOW() ON UPDATE NOW(),
        UNIQUE KEY uq_dev_date (device_id, work_date),
        INDEX idx_date (work_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    # 기존 배포에 target 컬럼 추가 (없으면 추가, 있으면 무시)
    """ALTER TABLE mes_daily_summary
       ADD COLUMN IF NOT EXISTS target INT DEFAULT NULL""",
]


def init_mes_db(app):
    """앱 컨텍스트 안에서 테이블 생성 + MQTT 구독 스레드 시작"""
    global _mqtt_app
    _mqtt_app = app
    with app.app_context():
        try:
            conn = _conn()
            cur  = conn.cursor()
            for sql in MES_INIT_SQL:
                cur.execute(sql)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[MES] DB 초기화 오류: {e}")
    _start_subscriber_if_leader()


def _start_subscriber_if_leader():
    """gunicorn 멀티워커(4개) 환경에서 MQTT 구독자가 '정확히 1개'만 뜨도록
    파일 락으로 리더를 선출한다.

    [배경] 워커마다 init_mes_db()가 실행되어 구독자 스레드가 4개 떴고,
    모두 같은 client ID("mes_subscriber")로 브로커에 붙어 서로를 끊어내며
    재접속을 반복 → QoS 0 메시지 대량 유실(실측 10건 중 2건만 저장).
    [해결] 락을 잡은 워커 1개만 구독, 나머지는 스킵 → 단일 소비자 보장.
    """
    global _mqtt_lock_fd
    try:
        _mqtt_lock_fd = open("/tmp/mes_mqtt_subscriber.lock", "w")
        fcntl.flock(_mqtt_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        # 다른 워커가 이미 구독 리더 → 이 워커는 구독하지 않음
        if _mqtt_lock_fd:
            _mqtt_lock_fd.close()
            _mqtt_lock_fd = None
        print(f"[MQTT] 구독자 미기동 (PID {os.getpid()}) — 다른 워커가 리더")
        return
    print(f"[MQTT] 구독자 리더 선출 (PID {os.getpid()})")
    threading.Thread(target=_start_mqtt_subscriber, daemon=True).start()


# ── MQTT 구독 ─────────────────────────────────────────────
def _mqtt_save_count(device_id, count):
    """receive_count()의 DB 저장 로직 재사용"""
    if not device_id:
        return
    try:
        with _mqtt_app.app_context():
            conn = _conn()
            try:
                cur  = conn.cursor()
                cur.execute("""
                    INSERT IGNORE INTO mes_devices (device_id, name)
                    VALUES (%s, %s)
                """, (device_id, device_id))
                cur.execute("""
                    INSERT INTO mes_label_count (device_id, count)
                    VALUES (%s, %s)
                """, (device_id, count))
                # NOTE: Python date.today()는 컨테이너 타임존(UTC) 의존 → DB의 CURDATE()(KST) 사용
                cur.execute("SELECT target_day FROM mes_devices WHERE device_id=%s LIMIT 1", (device_id,))
                _trow = cur.fetchone()
                _dev_target = (_trow[0] if _trow else 0) or 0
                cur.execute("""
                    INSERT INTO mes_daily_summary (device_id, work_date, total_count, peak_count, peak_hour, target)
                    SELECT %s, CURDATE(),
                           COALESCE(SUM(count), 0),
                           COALESCE(MAX(count), 0),
                           HOUR(recorded_at),
                           %s
                    FROM mes_label_count
                    WHERE device_id=%s AND DATE(recorded_at)=CURDATE()
                    GROUP BY device_id, DATE(recorded_at), HOUR(recorded_at)
                    ORDER BY SUM(count) DESC LIMIT 1
                    ON DUPLICATE KEY UPDATE
                        total_count = VALUES(total_count),
                        peak_count  = VALUES(peak_count),
                        peak_hour   = VALUES(peak_hour),
                        target      = COALESCE(target, VALUES(target)),
                        updated_at  = NOW()
                """, (device_id, _dev_target, device_id))
                conn.commit()
                print(f"[MQTT] count 저장: {device_id} = {count}")
            finally:
                conn.close()
    except Exception as e:
        print(f"[MQTT] count 저장 오류: {e}")


def _mqtt_save_env(device_id, temperature, humidity):
    """receive_env()의 DB 저장 로직 재사용"""
    if not device_id or temperature is None or humidity is None:
        return
    try:
        temperature = float(temperature)
        humidity    = float(humidity)
    except (ValueError, TypeError):
        print(f"[MQTT] env 값 오류: temp={temperature}, humi={humidity}")
        return
    if not (-40 <= temperature <= 80) or not (0 <= humidity <= 100):
        print(f"[MQTT] env 범위 초과: temp={temperature}, humi={humidity}")
        return
    try:
        with _mqtt_app.app_context():
            conn = _conn()
            try:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO mes_env_log (device_id, temperature, humidity)
                    VALUES (%s, %s, %s)
                """, (device_id, temperature, humidity))
                conn.commit()
                print(f"[MQTT] env 저장: {device_id} temp={temperature} humi={humidity}")
            finally:
                conn.close()
    except Exception as e:
        print(f"[MQTT] env 저장 오류: {e}")


def _mqtt_on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe([("mes/count", 1), ("mes/env", 1)])
        print("[MQTT] 구독 시작: mes/count, mes/env")
    else:
        print(f"[MQTT] 브로커 연결 거부 rc={rc}")


def _mqtt_on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        if msg.topic == "mes/count":
            _mqtt_save_count(
                str(data.get("device_id", "")).strip(),
                int(data.get("count", 0))
            )
        elif msg.topic == "mes/env":
            _mqtt_save_env(
                str(data.get("device_id", "")).strip(),
                data.get("temperature"),
                data.get("humidity")
            )
    except Exception as e:
        print(f"[MQTT] 처리 오류: {e}")


def _start_mqtt_subscriber():
    import time as _time
    delay = 5
    while True:
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, f"mes_subscriber_{os.getpid()}")
            client.on_connect = _mqtt_on_connect
            client.on_message = _mqtt_on_message
            client.connect("192.168.100.11", 1883, 60)
            delay = 5
            client.loop_forever()
            print("[MQTT] loop_forever 종료 — 재연결 시도")
        except Exception as e:
            print(f"[MQTT] 연결 실패 ({e}) — {delay}초 후 재시도")
        _time.sleep(delay)
        delay = min(delay * 2, 120)


# ── 인증 데코레이터 ───────────────────────────────────────
def _login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def _admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            flash("관리자만 접근 가능합니다.", "danger")
            return redirect(url_for("mes.realtime"))
        return f(*args, **kwargs)
    return wrapper


# ── ESP32 수신 엔드포인트 ──────────────────────────────────
@mes_bp.route("/count", methods=["POST"])
def receive_count():
    """
    ESP32 → POST /mes/count
    Body (JSON): {"device_id":"LINE_A","count":1234,"token":"..."}
    Body (form): device_id=LINE_A&count=1234&token=...
    """
    mes_token = current_app.config.get("MES_TOKEN", "")

    # JSON / form 모두 지원
    if request.is_json:
        data = request.get_json(force=True, silent=True) or {}
    else:
        data = request.form.to_dict()

    # 토큰 검증 (설정된 경우에만)
    if mes_token:
        incoming = data.get("token", request.headers.get("X-MES-Token", ""))
        if incoming != mes_token:
            return jsonify(ok=False, error="unauthorized"), 401

    device_id = str(data.get("device_id", "")).strip()
    try:
        count = int(data.get("count", 0))
    except (ValueError, TypeError):
        return jsonify(ok=False, error="invalid count"), 400

    if not device_id:
        return jsonify(ok=False, error="device_id required"), 400

    try:
        conn = _conn()
        cur  = conn.cursor()
        # 장치 자동 등록
        cur.execute("""
            INSERT IGNORE INTO mes_devices (device_id, name)
            VALUES (%s, %s)
        """, (device_id, device_id))

        # 카운트 저장
        cur.execute("""
            INSERT INTO mes_label_count (device_id, count)
            VALUES (%s, %s)
        """, (device_id, count))

        # 일별 집계 UPSERT — CURDATE()로 DB 타임존 기준 (KST) 사용
        cur.execute("SELECT target_day FROM mes_devices WHERE device_id=%s LIMIT 1", (device_id,))
        _trow = cur.fetchone()
        _dev_target = (_trow[0] if _trow else 0) or 0
        cur.execute("""
            INSERT INTO mes_daily_summary (device_id, work_date, total_count, peak_count, peak_hour, target)
            SELECT %s, CURDATE(),
                   COALESCE(SUM(count), 0),
                   COALESCE(MAX(count), 0),
                   HOUR(recorded_at),
                   %s
            FROM mes_label_count
            WHERE device_id=%s AND DATE(recorded_at)=CURDATE()
            GROUP BY device_id, DATE(recorded_at), HOUR(recorded_at)
            ORDER BY SUM(count) DESC LIMIT 1
            ON DUPLICATE KEY UPDATE
                total_count = VALUES(total_count),
                peak_count  = VALUES(peak_count),
                peak_hour   = VALUES(peak_hour),
                target      = COALESCE(target, VALUES(target)),
                updated_at  = NOW()
        """, (device_id, _dev_target, device_id))

        conn.commit()
        conn.close()
        return jsonify(ok=True, device_id=device_id, count=count)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


# ── 신규 실시간 대시보드 API (단일 라인용) ─────────────────
@mes_bp.route("/api/realtime")
@_login_required
def api_realtime():
    """쉴드라벨 카운터 대시보드용 올인원 API"""
    from datetime import datetime as _dt, timedelta as _td
    try:
        conn = _conn()
        cur  = conn.cursor()

        # 1) 전체 활성 장치 목록 (탭용)
        cur.execute("""
            SELECT device_id, name, line_name FROM mes_devices
            WHERE is_active = 1 AND device_id != '전산실' ORDER BY id
        """)
        all_devices = [
            {"device_id": r[0], "name": r[1] or r[0], "line_name": r[2] or ""}
            for r in cur.fetchall()
        ]
        if not all_devices:
            conn.close()
            return jsonify(ok=False, error="no_device")

        # 2) 선택된 장치 (?device_id=XXX 지정, 없으면 첫 번째)
        req_device = str(request.args.get("device_id", "")).strip()
        selected = None
        if req_device:
            for d in all_devices:
                if d["device_id"] == req_device:
                    selected = d; break
        if not selected:
            selected = all_devices[0]

        cur.execute("""
            SELECT device_id, name, line_name, target_day, title
            FROM mes_devices WHERE device_id=%s LIMIT 1
        """, (selected["device_id"],))
        dev = cur.fetchone()
        device_id, dev_name, line_name, target_day, dev_title = dev

        now    = _dt.now()
        today  = now.date()
        ystd   = today - _td(days=1)

        # 2) 오늘/어제 합계
        cur.execute("""
            SELECT
              SUM(CASE WHEN DATE(recorded_at)=%s THEN count ELSE 0 END) AS today_sum,
              SUM(CASE WHEN DATE(recorded_at)=%s THEN count ELSE 0 END) AS ystd_sum,
              MAX(recorded_at)
            FROM mes_label_count
            WHERE device_id=%s AND recorded_at >= %s
        """, (today, ystd, device_id, ystd.isoformat()))
        today_sum, ystd_sum, last_recv = cur.fetchone()
        today_sum = int(today_sum or 0)
        ystd_sum  = int(ystd_sum  or 0)

        # 3) 최근 1시간 합계
        hour_ago = now - _td(hours=1)
        cur.execute("""
            SELECT COALESCE(SUM(count),0) FROM mes_label_count
            WHERE device_id=%s AND recorded_at >= %s
        """, (device_id, hour_ago))
        last_hour = int(cur.fetchone()[0])

        # 4) 이번 주 (월~일 기준, 월요일 시작)
        week_start = today - _td(days=today.weekday())
        cur.execute("""
            SELECT DATE(recorded_at) AS d, COALESCE(SUM(count),0)
            FROM mes_label_count
            WHERE device_id=%s AND DATE(recorded_at) >= %s
            GROUP BY DATE(recorded_at)
        """, (device_id, week_start))
        week_map = {str(r[0]): int(r[1]) for r in cur.fetchall()}
        week_days = ["월", "화", "수", "목", "금", "토", "일"]
        weekly = []
        week_total = 0
        for i in range(7):
            d = week_start + _td(days=i)
            v = week_map.get(str(d), 0)
            week_total += v
            weekly.append({"day": week_days[i], "date": str(d), "total": v, "is_today": d == today})

        # 5) 시간대별 오늘 (0~23시)
        cur.execute("""
            SELECT HOUR(recorded_at) AS hr, COALESCE(SUM(count),0)
            FROM mes_label_count
            WHERE device_id=%s AND DATE(recorded_at)=%s
            GROUP BY hr
        """, (device_id, today))
        hour_map = {int(r[0]): int(r[1]) for r in cur.fetchall()}
        hourly = [hour_map.get(h, 0) for h in range(24)]

        # 6) 최근 감지 로그 (최근 20건)
        cur.execute("""
            SELECT id, count, recorded_at FROM mes_label_count
            WHERE device_id=%s AND DATE(recorded_at)=%s
            ORDER BY id DESC LIMIT 20
        """, (device_id, today))
        raw_logs = cur.fetchall()
        # 누적값 계산을 위해 오늘 최대 id까지의 누적합 필요
        recent_logs = []
        if raw_logs:
            # 오늘 각 id 이하의 누적 합
            ids = [r[0] for r in raw_logs]
            min_id = min(ids)
            cur.execute("""
                SELECT id, count FROM mes_label_count
                WHERE device_id=%s AND DATE(recorded_at)=%s AND id <= %s
                ORDER BY id
            """, (device_id, today, max(ids)))
            running = 0
            cumulative_map = {}
            for row_id, cnt in cur.fetchall():
                running += int(cnt)
                cumulative_map[row_id] = running
            for row_id, cnt, rec_at in raw_logs:
                recent_logs.append({
                    "time": rec_at.strftime("%H:%M:%S"),
                    "count": int(cnt),
                    "cumulative": cumulative_map.get(row_id, 0),
                })

        # 7) 센서 상태 판정 (마지막 수신 경과시간)
        sensor_status = "off"
        last_recv_str = "-"
        last_recv_rel = "-"
        if last_recv:
            delta_sec = (now - last_recv).total_seconds()
            if delta_sec < 300:   sensor_status = "live"
            elif delta_sec < 900: sensor_status = "warn"
            else:                 sensor_status = "off"
            last_recv_str = last_recv.strftime("%Y-%m-%d %H:%M:%S")
            if   delta_sec < 60:   last_recv_rel = "방금 전"
            elif delta_sec < 3600: last_recv_rel = f"{int(delta_sec/60)}분 전"
            elif delta_sec < 86400:last_recv_rel = f"{int(delta_sec/3600)}시간 전"
            else:                  last_recv_rel = last_recv.strftime("%m-%d")

        # 8) % vs 어제
        if ystd_sum > 0:
            pct = round((today_sum - ystd_sum) / ystd_sum * 100, 1)
        elif today_sum > 0:
            pct = None  # 어제 0, 오늘 > 0 → 비교 불가
        else:
            pct = 0.0

        # 9) 분당 속도 (최근 1시간 기준)
        per_minute = round(last_hour / 60.0, 1) if last_hour else 0.0

        conn.close()
        return jsonify(
            ok=True,
            ts=now.strftime("%H:%M:%S"),
            date=today.isoformat(),
            all_devices=all_devices,
            device={
                "device_id": device_id,
                "name": dev_name or device_id,
                "line_name": line_name or "",
                "target": target_day or 0,
                "title": dev_title or "",
            },
            kpi={
                "today":        today_sum,
                "yesterday":    ystd_sum,
                "pct":          pct,
                "last_hour":    last_hour,
                "per_minute":   per_minute,
                "week_total":   week_total,
                "sensor_status": sensor_status,
                "last_recv_rel": last_recv_rel,
                "last_recv":     last_recv_str,
            },
            hourly=hourly,
            weekly=weekly,
            recent=recent_logs,
            system={
                "device_id":   device_id,
                "transport":   "ESP32 / MQTT",
                "endpoint":    "mes/count",
                "last_recv":   last_recv_rel,
                "db_status":   "정상",
            },
        )
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


# ── 장치 정보 일괄 변경 (관리자 전용, 부분 업데이트) ────────
@mes_bp.route("/api/update_device", methods=["POST"])
@_admin_required
def api_update_device():
    """
    지정된 필드만 업데이트. 빈 문자열 덮어쓰기 위험 방지.
    Body: {"device_id":"LINE_A", "name":"EBS Fb", "line_name":"A", "target":2500}
    키가 포함되지 않은 필드는 건드리지 않음.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        device_id = str(data.get("device_id", "")).strip()
        if not device_id:
            return jsonify(ok=False, error="device_id required"), 400

        updates, params = [], []
        if "name" in data:
            updates.append("name = %s")
            params.append(str(data["name"]))  # strip 하지 않음 (의도적 공백 허용)
        if "line_name" in data:
            updates.append("line_name = %s")
            params.append(str(data["line_name"]))
        if "title" in data:
            updates.append("title = %s")
            t = str(data["title"]).strip()
            params.append(t if t else None)  # 빈 문자열은 NULL로 저장 → 기본값 사용
        if "target" in data:
            try:
                t = int(data["target"] or 0)
            except (ValueError, TypeError):
                return jsonify(ok=False, error="invalid target"), 400
            if t < 0:
                return jsonify(ok=False, error="target must be >= 0"), 400
            updates.append("target_day = %s")
            params.append(t)

        if not updates:
            return jsonify(ok=False, error="no fields to update"), 400

        params.append(device_id)
        conn = _conn(); cur = conn.cursor()
        cur.execute(
            f"UPDATE mes_devices SET {', '.join(updates)} WHERE device_id = %s",
            params
        )
        updated = cur.rowcount
        conn.commit()
        conn.close()
        if updated == 0:
            return jsonify(ok=False, error="device not found"), 404
        return jsonify(ok=True, device_id=device_id, updated_fields=list(data.keys()))
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


# ── 일 목표 변경 (관리자 전용) ─────────────────────────────
@mes_bp.route("/api/update_target", methods=["POST"])
@_admin_required
def api_update_target():
    """일 목표치만 업데이트 (다른 필드는 건드리지 않음)"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        device_id = str(data.get("device_id", "")).strip()
        try:
            target = int(data.get("target", 0) or 0)
        except (ValueError, TypeError):
            return jsonify(ok=False, error="invalid target"), 400
        if target < 0:
            return jsonify(ok=False, error="target must be >= 0"), 400
        if not device_id:
            return jsonify(ok=False, error="device_id required"), 400

        conn = _conn(); cur = conn.cursor()
        cur.execute("""
            UPDATE mes_devices SET target_day=%s WHERE device_id=%s
        """, (target, device_id))
        updated = cur.rowcount
        # 오늘 daily summary에도 반영 (다음날 carry-over의 기준이 됨)
        cur.execute("""
            INSERT INTO mes_daily_summary (device_id, work_date, target)
            VALUES (%s, CURDATE(), %s)
            ON DUPLICATE KEY UPDATE target=%s
        """, (device_id, target, target))
        conn.commit()
        conn.close()
        if updated == 0:
            return jsonify(ok=False, error="device not found"), 404
        return jsonify(ok=True, device_id=device_id, target=target)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


# ── 오늘 카운트 리셋 (관리자 전용) ─────────────────────────
@mes_bp.route("/api/reset_today", methods=["POST"])
@_admin_required
def api_reset_today():
    """오늘 카운트 전체 삭제"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        device_id = str(data.get("device_id", "")).strip()
        if not device_id:
            # 없으면 첫 번째 활성 장치
            conn = _conn(); cur = conn.cursor()
            cur.execute("SELECT device_id FROM mes_devices WHERE is_active=1 ORDER BY id LIMIT 1")
            row = cur.fetchone()
            conn.close()
            if not row:
                return jsonify(ok=False, error="no_device"), 400
            device_id = row[0]

        conn = _conn(); cur = conn.cursor()
        cur.execute("""
            DELETE FROM mes_label_count
            WHERE device_id=%s AND DATE(recorded_at)=CURDATE()
        """, (device_id,))
        rows_deleted = cur.rowcount
        cur.execute("""
            DELETE FROM mes_daily_summary
            WHERE device_id=%s AND work_date=CURDATE()
        """, (device_id,))
        conn.commit()
        conn.close()
        return jsonify(ok=True, device_id=device_id, deleted=rows_deleted)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


# ── 실시간 상태 API (polling) ──────────────────────────────
@mes_bp.route("/api/status")
@_login_required
def api_status():
    """5초마다 실시간 화면에서 polling"""
    try:
        conn = _conn()
        cur  = conn.cursor()
        today = date.today().isoformat()

        # 장치별 최신 카운트 + 오늘 합계 (mes_label_count 직접 집계)
        cur.execute("""
            SELECT
                d.device_id,
                d.name,
                d.line_name,
                d.target_day,
                d.title,
                COALESCE(
                    (SELECT SUM(l.count) FROM mes_label_count l
                     WHERE l.device_id = d.device_id AND DATE(l.recorded_at) = %s), 0
                ) AS today_total,
                COALESCE(s.peak_count,  0)  AS peak_count,
                COALESCE(s.peak_hour,  -1)  AS peak_hour,
                (SELECT count FROM mes_label_count l
                 WHERE l.device_id = d.device_id
                 ORDER BY recorded_at DESC LIMIT 1)  AS latest_count,
                (SELECT recorded_at FROM mes_label_count l
                 WHERE l.device_id = d.device_id
                 ORDER BY recorded_at DESC LIMIT 1)  AS latest_time
            FROM mes_devices d
            LEFT JOIN mes_daily_summary s
                   ON s.device_id = d.device_id AND s.work_date = %s
            WHERE d.is_active = 1 AND d.device_id != '전산실'
            ORDER BY d.line_name, d.name
        """, (today, today))

        rows = []
        for r in cur.fetchall():
            device_id, name, line_name, target, title, today_total, peak_count, peak_hour, latest, ltime = r
            target      = int(target      or 0)
            today_total = int(today_total or 0)
            peak_count  = int(peak_count  or 0)
            latest      = int(latest      or 0)
            pct = round(today_total / target * 100, 1) if target else 0
            rows.append({
                "device_id":   device_id,
                "name":        name or device_id,
                "line_name":   line_name or "-",
                "title":       title or "",
                "target":      target,
                "today_total": today_total,
                "peak_count":  peak_count,
                "peak_hour":   int(peak_hour) if peak_hour is not None else -1,
                "latest":      latest,
                "latest_time": ltime.strftime("%H:%M:%S") if ltime else "-",
                "pct":         min(pct, 100),
                "over_target": pct >= 100,
            })
        conn.close()
        return jsonify(ok=True, devices=rows, ts=datetime.now().strftime("%H:%M:%S"))
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@mes_bp.route("/api/hourly")
@_login_required
def api_hourly():
    """시간별 전체 생산량 합계 (페이스메이커 추이 차트용)"""
    try:
        conn = _conn()
        cur  = conn.cursor()
        today = date.today().isoformat()
        cur.execute("""
            SELECT HOUR(recorded_at) AS h, SUM(count) AS total
            FROM mes_label_count
            WHERE DATE(recorded_at) = %s
            GROUP BY HOUR(recorded_at)
            ORDER BY h
        """, (today,))
        hourly = {int(r[0]): int(r[1]) for r in cur.fetchall()}
        conn.close()
        return jsonify(ok=True, hourly=hourly, current_hour=datetime.now().hour)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


# ── 실시간 화면 ────────────────────────────────────────────
@mes_bp.route("/realtime")
@_login_required
def realtime():
    return render_template("mes_realtime.html", active_page="mes_realtime")


# ── 리포트 ─────────────────────────────────────────────────
@mes_bp.route("/report")
@_login_required
def report():
    mode       = request.args.get("mode", "daily")   # daily / monthly
    sel_date   = request.args.get("date",  date.today().isoformat())
    sel_month  = request.args.get("month", date.today().strftime("%Y-%m"))
    device_filter = request.args.get("device", "")

    rows = []
    chart_labels, chart_datasets = [], []
    devices = []

    try:
        conn = _conn()
        cur  = conn.cursor()

        # 장치 목록
        cur.execute("SELECT device_id, name, line_name FROM mes_devices WHERE is_active=1 AND device_id != '전산실' ORDER BY line_name, name")
        devices = [{"device_id": r[0], "name": r[1] or r[0], "line_name": r[2] or ""} for r in cur.fetchall()]

        if mode == "daily":
            # 일별: 선택한 날짜의 시간대별 카운트
            where_dev = "AND device_id=%s" if device_filter else ""
            params    = [sel_date, device_filter] if device_filter else [sel_date]
            cur.execute(f"""
                SELECT device_id, HOUR(recorded_at) AS hr, SUM(count) AS cnt
                FROM mes_label_count
                WHERE DATE(recorded_at) = %s {where_dev}
                GROUP BY device_id, hr
                ORDER BY device_id, hr
            """, params)
            raw = cur.fetchall()

            # 장치별 시간대 집계
            dev_map = {}
            for device_id, hr, cnt in raw:
                dev_map.setdefault(device_id, {})
                dev_map[device_id][int(hr)] = int(cnt)

            chart_labels = [f"{h:02d}시" for h in range(24)]
            colors = [
                "rgba(0,113,227,.7)", "rgba(52,199,89,.7)", "rgba(255,149,0,.7)",
                "rgba(255,59,48,.7)", "rgba(90,200,250,.7)", "rgba(175,82,222,.7)",
            ]
            for i, (dev_id, hr_map) in enumerate(dev_map.items()):
                dev_name = next((d["name"] for d in devices if d["device_id"] == dev_id), dev_id)
                chart_datasets.append({
                    "label": dev_name,
                    "data":  [hr_map.get(h, 0) for h in range(24)],
                    "backgroundColor": colors[i % len(colors)],
                    "borderRadius": 3,
                })

            # 테이블: 일별 집계
            cur.execute(f"""
                SELECT device_id, SUM(count) AS total, MAX(count) AS peak
                FROM mes_label_count
                WHERE DATE(recorded_at) = %s {where_dev}
                GROUP BY device_id
                ORDER BY total DESC
            """, params)
            for device_id, total, peak in cur.fetchall():
                dev_name = next((d["name"] for d in devices if d["device_id"] == device_id), device_id)
                line     = next((d["line_name"] for d in devices if d["device_id"] == device_id), "")
                rows.append({"device_id": device_id, "name": dev_name, "line": line,
                              "total": int(total), "peak": int(peak)})

        else:  # monthly
            # 월별: 선택한 월의 일별 합계
            ym = sel_month.replace("-", "")[:6]
            where_dev = "AND device_id=%s" if device_filter else ""
            params    = [ym + "%", device_filter] if device_filter else [ym + "%"]
            cur.execute(f"""
                SELECT device_id, work_date, total_count
                FROM mes_daily_summary
                WHERE DATE_FORMAT(work_date,'%%Y%%m') = %s {where_dev.replace('%s','%s') if device_filter else ''}
                ORDER BY device_id, work_date
            """, [ym, device_filter] if device_filter else [ym])

            dev_day_map = {}
            all_dates   = set()
            for device_id, work_date, total in cur.fetchall():
                dev_day_map.setdefault(device_id, {})
                dev_day_map[device_id][str(work_date)] = int(total)
                all_dates.add(str(work_date))

            chart_labels = sorted(all_dates)
            short_labels = [d[5:] for d in chart_labels]  # MM-DD
            colors = [
                "rgba(0,113,227,.7)", "rgba(52,199,89,.7)", "rgba(255,149,0,.7)",
                "rgba(255,59,48,.7)", "rgba(90,200,250,.7)",
            ]
            for i, (dev_id, day_map) in enumerate(dev_day_map.items()):
                dev_name = next((d["name"] for d in devices if d["device_id"] == dev_id), dev_id)
                chart_datasets.append({
                    "label": dev_name,
                    "data":  [day_map.get(d, 0) for d in chart_labels],
                    "backgroundColor": colors[i % len(colors)],
                    "borderRadius": 3,
                })

            # 월별 장치 합계 테이블
            cur.execute(f"""
                SELECT device_id, SUM(total_count), MAX(peak_count)
                FROM mes_daily_summary
                WHERE DATE_FORMAT(work_date,'%%Y%%m') = %s {where_dev}
                GROUP BY device_id ORDER BY SUM(total_count) DESC
            """, [ym, device_filter] if device_filter else [ym])
            for device_id, total, peak in cur.fetchall():
                dev_name = next((d["name"] for d in devices if d["device_id"] == device_id), device_id)
                line     = next((d["line_name"] for d in devices if d["device_id"] == device_id), "")
                rows.append({"device_id": device_id, "name": dev_name, "line": line,
                              "total": int(total), "peak": int(peak)})

            chart_labels = short_labels

        conn.close()
    except Exception as e:
        flash(f"리포트 조회 오류: {e}", "danger")

    return render_template(
        "mes_report.html",
        mode=mode, sel_date=sel_date, sel_month=sel_month,
        device_filter=device_filter, devices=devices,
        rows=rows,
        chart_labels=chart_labels,
        chart_datasets=chart_datasets,
        active_page="mes_report",
    )


# ── 장치 관리 (admin) ──────────────────────────────────────
@mes_bp.route("/devices", methods=["GET", "POST"])
@_admin_required
def device_mgmt():
    msg = None
    if request.method == "POST":
        action    = request.form.get("action", "")
        device_id = request.form.get("device_id", "").strip()
        try:
            conn = _conn()
            cur  = conn.cursor()
            if action == "add":
                name      = request.form.get("name", device_id).strip()
                line_name = request.form.get("line_name", "").strip()
                target    = int(request.form.get("target_day", 2000) or 2000)
                cur.execute("""
                    INSERT INTO mes_devices (device_id, name, line_name, target_day)
                    VALUES (%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE name=%s, line_name=%s, target_day=%s, is_active=1
                """, (device_id, name, line_name, target, name, line_name, target))
                msg = f"장치 '{name}' 저장됨"
            elif action == "toggle":
                cur.execute("UPDATE mes_devices SET is_active = 1 - is_active WHERE device_id=%s", (device_id,))
                msg = "상태 변경됨"
            elif action == "delete":
                cur.execute("DELETE FROM mes_devices WHERE device_id=%s", (device_id,))
                msg = "삭제됨"
            conn.commit()
            conn.close()
        except Exception as e:
            flash(f"오류: {e}", "danger")

    try:
        conn = _conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT d.device_id, d.name, d.line_name, d.target_day, d.is_active,
                   COUNT(l.id) AS total_records,
                   MAX(l.recorded_at) AS last_seen
            FROM mes_devices d
            LEFT JOIN mes_label_count l ON l.device_id = d.device_id
            GROUP BY d.device_id, d.name, d.line_name, d.target_day, d.is_active
            ORDER BY d.line_name, d.name
        """)
        devices = [
            {
                "device_id":     r[0],
                "name":          r[1] or r[0],
                "line_name":     r[2] or "-",
                "target_day":    r[3],
                "is_active":     r[4],
                "total_records": r[5],
                "last_seen":     r[6].strftime("%Y-%m-%d %H:%M") if r[6] else "-",
            }
            for r in cur.fetchall()
        ]
        conn.close()
    except Exception as e:
        devices = []
        flash(f"조회 오류: {e}", "danger")

    return render_template(
        "mes_devices.html",
        devices=devices, msg=msg,
        active_page="mes_devices",
    )


# ── ESP32 온습도 수신 ──────────────────────────────────────
@mes_bp.route("/env", methods=["POST"])
def receive_env():
    """
    ESP32 → POST /mes/env
    Body (JSON): {"device_id":"LINE_A","temperature":25.3,"humidity":60.1}
    """
    if request.is_json:
        data = request.get_json(force=True, silent=True) or {}
    else:
        data = request.form.to_dict()

    device_id = str(data.get("device_id", "")).strip()
    if not device_id:
        return jsonify(ok=False, error="device_id required"), 400

    try:
        temperature = float(data.get("temperature", 0))
        humidity    = float(data.get("humidity", 0))
    except (ValueError, TypeError):
        return jsonify(ok=False, error="invalid values"), 400

    if not (-40 <= temperature <= 80) or not (0 <= humidity <= 100):
        return jsonify(ok=False, error="out of range"), 400

    try:
        conn = _conn(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO mes_env_log (device_id, temperature, humidity)
            VALUES (%s, %s, %s)
        """, (device_id, temperature, humidity))
        conn.commit()
        conn.close()
        return jsonify(ok=True, device_id=device_id, temperature=temperature, humidity=humidity)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


# ── 온습도 API ─────────────────────────────────────────────
@mes_bp.route("/api/env")
@_login_required
def api_env():
    """온습도 대시보드용 데이터 API"""
    from datetime import datetime as _dt, timedelta as _td
    device_id = str(request.args.get("device_id", "전산실")).strip()
    try:
        conn = _conn(); cur = conn.cursor()
        now = _dt.now()
        today = now.date()

        # 최신값
        cur.execute("""
            SELECT temperature, humidity, recorded_at
            FROM mes_env_log WHERE device_id=%s
            ORDER BY id DESC LIMIT 1
        """, (device_id,))
        row = cur.fetchone()
        latest = {}
        if row:
            delta = (now - row[2]).total_seconds()
            latest = {
                "temperature": round(row[0], 1),
                "humidity":    round(row[1], 1),
                "recorded_at": row[2].strftime("%Y-%m-%d %H:%M:%S"),
                "age": "방금 전" if delta < 60 else f"{int(delta/60)}분 전" if delta < 3600 else f"{int(delta/3600)}시간 전",
                "status": "live" if delta < 300 else "warn" if delta < 900 else "off",
            }

        # 오늘 시간대별 평균
        cur.execute("""
            SELECT HOUR(recorded_at) AS hr,
                   ROUND(AVG(temperature),1), ROUND(AVG(humidity),1)
            FROM mes_env_log
            WHERE device_id=%s AND DATE(recorded_at)=%s
            GROUP BY hr ORDER BY hr
        """, (device_id, today))
        hour_map_t = {}; hour_map_h = {}
        for hr, t, h in cur.fetchall():
            hour_map_t[int(hr)] = t
            hour_map_h[int(hr)] = h
        hourly_temp = [hour_map_t.get(h) for h in range(24)]
        hourly_humi = [hour_map_h.get(h) for h in range(24)]

        # 최근 20건 로그
        cur.execute("""
            SELECT temperature, humidity, recorded_at
            FROM mes_env_log WHERE device_id=%s
            ORDER BY id DESC LIMIT 20
        """, (device_id,))
        recent = [
            {"temperature": round(r[0],1), "humidity": round(r[1],1),
             "time": r[2].strftime("%H:%M:%S")}
            for r in cur.fetchall()
        ]

        # 최근 30일 일별 평균
        cur.execute("""
            SELECT DATE(recorded_at) AS d,
                   ROUND(AVG(temperature),1), ROUND(AVG(humidity),1)
            FROM mes_env_log
            WHERE device_id=%s AND recorded_at >= DATE_SUB(CURDATE(), INTERVAL 29 DAY)
            GROUP BY d ORDER BY d
        """, (device_id,))
        daily_rows = cur.fetchall()
        daily_labels = [str(r[0])[5:].replace('-', '/') for r in daily_rows]
        daily_temp   = [r[1] for r in daily_rows]
        daily_humi   = [r[2] for r in daily_rows]

        # 오늘 최저/최고
        cur.execute("""
            SELECT MIN(temperature), MAX(temperature), MIN(humidity), MAX(humidity)
            FROM mes_env_log WHERE device_id=%s AND DATE(recorded_at)=%s
        """, (device_id, today))
        stat = cur.fetchone()
        stats = {
            "temp_min": round(stat[0],1) if stat[0] is not None else None,
            "temp_max": round(stat[1],1) if stat[1] is not None else None,
            "humi_min": round(stat[2],1) if stat[2] is not None else None,
            "humi_max": round(stat[3],1) if stat[3] is not None else None,
        }

        # 등록된 장치 목록 (mes_devices.name 우선, 없으면 device_id)
        cur.execute("""
            SELECT DISTINCT l.device_id,
                   COALESCE(NULLIF(d.name, ''), l.device_id) AS label
            FROM mes_env_log l
            LEFT JOIN mes_devices d ON d.device_id = l.device_id
            ORDER BY label
        """)
        devices = [{"device_id": r[0], "label": r[1]} for r in cur.fetchall()]

        conn.close()
        return jsonify(ok=True, latest=latest, hourly_temp=hourly_temp,
                       hourly_humi=hourly_humi, recent=recent, stats=stats,
                       devices=devices, ts=now.strftime("%H:%M:%S"),
                       daily_labels=daily_labels, daily_temp=daily_temp,
                       daily_humi=daily_humi)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


# ── 온습도 대시보드 페이지 ─────────────────────────────────
@mes_bp.route("/env/dashboard")
@_login_required
def env_dashboard():
    return render_template("mes_env_dashboard.html", active_page="mes_env")
