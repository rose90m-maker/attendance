"""안전관리 — 위험물 관리 + 자동 알림 Blueprint"""
import os, time, threading, json
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, jsonify, session, redirect, flash
import pymysql

hazmat_bp = Blueprint("hazmat", __name__, url_prefix="/hazmat")

def _conn():
    from app_maria import MARIA
    return pymysql.connect(**MARIA)

def _login_required(f):
    from functools import wraps
    @wraps(f)
    def deco(*a, **kw):
        if not session.get("user_id"):
            return redirect("/login")
        return f(*a, **kw)
    return deco

def _admin_required(f):
    from functools import wraps
    @wraps(f)
    def deco(*a, **kw):
        if not session.get("user_id"):
            return redirect("/login")
        if session.get("role") != "admin":
            flash("관리자 전용입니다.", "danger")
            return redirect("/")
        return f(*a, **kw)
    return deco


# ── DB 초기화 ───────────────────────────────────────────────
def init_hazmat_db(app):
    with app.app_context():
        conn = _conn(); cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `hazmat_items` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `name` VARCHAR(100) NOT NULL,
                `category` VARCHAR(30) DEFAULT '',
                `quantity` DECIMAL(10,2),
                `unit` VARCHAR(10) DEFAULT '',
                `location` VARCHAR(100) DEFAULT '',
                `storage_temp_max` FLOAT,
                `storage_temp_min` FLOAT,
                `storage_humi_max` FLOAT,
                `sensor_device_id` VARCHAR(40) DEFAULT '',
                `camera_url` VARCHAR(300) DEFAULT '',
                `inspect_cycle_days` INT DEFAULT 30,
                `last_inspected` DATE,
                `next_inspect` DATE,
                `last_alert_sent` DATE,
                `is_active` TINYINT(1) DEFAULT 1,
                `note` TEXT,
                `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `hazmat_recipients` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `name` VARCHAR(50) NOT NULL,
                `phone` VARCHAR(20) DEFAULT '',
                `email` VARCHAR(100) DEFAULT '',
                `tg_chat_id` VARCHAR(50) DEFAULT '',
                `is_active` TINYINT(1) DEFAULT 1,
                `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `hazmat_alerts` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `hazmat_id` INT,
                `alert_type` VARCHAR(50),
                `title` VARCHAR(200),
                `message` TEXT,
                `sent_via` VARCHAR(100),
                `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX `idx_ha_time` (`created_at`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit(); conn.close()


# ── 카메라 스냅샷 ────────────────────────────────────────────
def _capture_snapshot(camera_url):
    """RTSP 또는 HTTP URL에서 스냅샷 캡처. 실패 시 None 반환."""
    if not camera_url:
        return None
    try:
        import tempfile, subprocess
        tmp = tempfile.mktemp(suffix=".jpg")
        if camera_url.startswith("rtsp://"):
            # ffmpeg으로 RTSP 스냅샷 1장 캡처
            r = subprocess.run(
                ["ffmpeg", "-y", "-rtsp_transport", "tcp",
                 "-i", camera_url, "-vframes", "1", "-q:v", "2", tmp],
                timeout=15, capture_output=True
            )
            if r.returncode != 0:
                return None
        else:
            # HTTP 스냅샷 URL 직접 다운로드
            import urllib.request
            urllib.request.urlretrieve(camera_url, tmp)
        return tmp if os.path.exists(tmp) and os.path.getsize(tmp) > 0 else None
    except Exception:
        return None


# ── 알림 발송 공통 함수 ──────────────────────────────────────
def _dispatch_hazmat_alert(alert_type, title, message, hazmat_id=None, camera_url=None):
    """수신자 전원에게 SMS + 이메일 + 텔레그램 발송 후 이력 저장"""
    try:
        from app_maria import (
            _send_telegram, _send_sms_solapi, _send_email,
            TELEGRAM_TOKEN, MAIL_USER
        )
        import ssl
        from urllib.request import urlopen, Request
        from urllib.parse import urlencode

        conn = _conn(); cur = conn.cursor()
        cur.execute("SELECT name, phone, email, tg_chat_id FROM hazmat_recipients WHERE is_active=1")
        recipients = cur.fetchall()
        conn.close()

        if not recipients:
            return

        phones  = [r[1] for r in recipients if r[1]]
        emails  = [r[2] for r in recipients if r[2]]
        tg_ids  = [r[3] for r in recipients if r[3]]

        full_msg = f"[안전관리 알림]\n{title}\n\n{message}"

        # 카메라 스냅샷
        img_path = _capture_snapshot(camera_url) if camera_url else None

        # 텔레그램
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        def _tg_send(chat_id, text, image=None):
            try:
                if image and os.path.exists(image):
                    import mimetypes
                    boundary = "----HazmatBoundary"
                    with open(image, "rb") as f:
                        img_data = f.read()
                    body = (
                        f"--{boundary}\r\n"
                        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
                        f"--{boundary}\r\n"
                        f'Content-Disposition: form-data; name="caption"\r\n\r\n{text}\r\n'
                        f"--{boundary}\r\n"
                        f'Content-Disposition: form-data; name="photo"; filename="snapshot.jpg"\r\n'
                        f'Content-Type: image/jpeg\r\n\r\n'
                    ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()
                    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
                    req = Request(url, data=body,
                                  headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
                    urlopen(req, context=ctx, timeout=15)
                else:
                    data = urlencode({"chat_id": chat_id, "text": text}).encode()
                    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                    urlopen(Request(url, data), context=ctx, timeout=10)
            except Exception:
                pass

        # 메인 채널
        from app_maria import TELEGRAM_CHAT_ID
        _tg_send(TELEGRAM_CHAT_ID, full_msg, img_path)
        for tid in tg_ids:
            _tg_send(tid, full_msg, img_path)

        # SMS
        sent_via = []
        if phones:
            r = _send_sms_solapi(phones, full_msg[:90])
            if r.get("ok"):
                sent_via.append("sms")

        # 이메일
        if emails:
            color = {"temp_high":"#dc2626","temp_low":"#2563eb","humi_high":"#7c3aed",
                     "smoke":"#dc2626","fire":"#dc2626","inspect_due":"#d97706",
                     "inspect_overdue":"#dc2626","manual":"#374151"}.get(alert_type, "#374151")
            body_html = f"""
            <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
              <div style="background:{color};color:#fff;padding:16px 20px;border-radius:8px 8px 0 0;">
                <h2 style="margin:0;font-size:1.1rem;">⚠️ {title}</h2>
              </div>
              <div style="background:#f9fafb;padding:16px 20px;border:1px solid #e5e7eb;border-radius:0 0 8px 8px;">
                <p style="white-space:pre-line;color:#374151;">{message}</p>
                <p style="font-size:0.8rem;color:#9ca3af;margin-top:16px;">
                  발송시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                </p>
              </div>
            </div>"""
            _send_email(emails, f"[안전관리] {title}", body_html)
            sent_via.append("email")

        if tg_ids or TELEGRAM_CHAT_ID:
            sent_via.append("telegram")

        # 이력 저장
        conn2 = _conn(); cur2 = conn2.cursor()
        cur2.execute("""INSERT INTO hazmat_alerts (hazmat_id, alert_type, title, message, sent_via)
                        VALUES (%s,%s,%s,%s,%s)""",
                     (hazmat_id, alert_type, title, message, ",".join(sent_via)))
        conn2.commit(); conn2.close()

        # 임시 스냅샷 파일 삭제
        if img_path and os.path.exists(img_path):
            try: os.remove(img_path)
            except Exception: pass

    except Exception as e:
        print(f"[HAZMAT] 알림 발송 오류: {e}")


# ── 백그라운드 모니터 ────────────────────────────────────────
_monitor_started = False

def _hazmat_monitor():
    """5분마다 온습도 임계값 + 점검 기한 체크"""
    import time as _time
    _time.sleep(30)  # 앱 시작 후 30초 대기
    while True:
        try:
            conn = _conn(); cur = conn.cursor()
            today = date.today()

            # ① 온습도 임계값 체크
            cur.execute("""SELECT id, name, location, storage_temp_max, storage_temp_min,
                                  storage_humi_max, sensor_device_id, camera_url
                           FROM hazmat_items
                           WHERE is_active=1 AND sensor_device_id != '' AND sensor_device_id IS NOT NULL""")
            items = cur.fetchall()
            for item in items:
                iid, iname, iloc, tmax, tmin, hmax, dev_id, cam_url = item
                if not dev_id:
                    continue
                cur.execute("""SELECT temperature, humidity FROM mes_env_log
                               WHERE device_id=%s ORDER BY recorded_at DESC LIMIT 1""", (dev_id,))
                env = cur.fetchone()
                if not env:
                    continue
                temp, humi = env

                if tmax is not None and temp > tmax:
                    _dispatch_hazmat_alert(
                        "temp_high",
                        f"🌡️ 온도 초과 — {iname}",
                        f"위치: {iloc}\n현재 온도: {temp}°C (허용 최고: {tmax}°C)\n즉시 확인이 필요합니다.",
                        hazmat_id=iid, camera_url=cam_url
                    )
                elif tmin is not None and temp < tmin:
                    _dispatch_hazmat_alert(
                        "temp_low",
                        f"🌡️ 온도 저하 — {iname}",
                        f"위치: {iloc}\n현재 온도: {temp}°C (허용 최저: {tmin}°C)\n즉시 확인이 필요합니다.",
                        hazmat_id=iid, camera_url=cam_url
                    )
                if hmax is not None and humi > hmax:
                    _dispatch_hazmat_alert(
                        "humi_high",
                        f"💧 습도 초과 — {iname}",
                        f"위치: {iloc}\n현재 습도: {humi}% (허용 최고: {hmax}%)\n즉시 확인이 필요합니다.",
                        hazmat_id=iid, camera_url=cam_url
                    )

            # ② 점검 기한 체크 (하루 1회)
            cur.execute("""SELECT id, name, location, next_inspect, last_alert_sent
                           FROM hazmat_items
                           WHERE is_active=1 AND next_inspect IS NOT NULL""")
            inspect_items = cur.fetchall()
            for item in inspect_items:
                iid, iname, iloc, next_dt, last_sent = item
                if not next_dt:
                    continue
                if last_sent and last_sent >= today:
                    continue  # 오늘 이미 발송함
                days_left = (next_dt - today).days
                if days_left == 7:
                    _dispatch_hazmat_alert(
                        "inspect_due",
                        f"📋 점검 기한 D-7 — {iname}",
                        f"위치: {iloc}\n점검 예정일: {next_dt}\n7일 후 점검 기한입니다. 일정을 확인하세요.",
                        hazmat_id=iid
                    )
                    cur.execute("UPDATE hazmat_items SET last_alert_sent=%s WHERE id=%s", (today, iid))
                elif days_left == 1:
                    _dispatch_hazmat_alert(
                        "inspect_due",
                        f"📋 점검 기한 내일 — {iname}",
                        f"위치: {iloc}\n점검 예정일: {next_dt}\n내일이 점검 기한입니다!",
                        hazmat_id=iid
                    )
                    cur.execute("UPDATE hazmat_items SET last_alert_sent=%s WHERE id=%s", (today, iid))
                elif days_left < 0:
                    _dispatch_hazmat_alert(
                        "inspect_overdue",
                        f"🚨 점검 기한 초과 — {iname}",
                        f"위치: {iloc}\n점검 예정일: {next_dt} ({abs(days_left)}일 초과)\n즉시 점검을 실시하세요!",
                        hazmat_id=iid
                    )
                    cur.execute("UPDATE hazmat_items SET last_alert_sent=%s WHERE id=%s", (today, iid))

            conn.commit(); conn.close()
        except Exception as e:
            print(f"[HAZMAT] 모니터 오류: {e}")
        _time.sleep(300)  # 5분


def start_hazmat_monitor():
    global _monitor_started
    if not _monitor_started:
        _monitor_started = True
        threading.Thread(target=_hazmat_monitor, daemon=True).start()
        print("[HAZMAT] 모니터 스레드 시작")


# ── 화재/연기 이벤트 훅 (app_maria.py에서 호출) ──────────────
def on_fire_event(device_key, device_name, location, event_type, status):
    """소방 이벤트 발생 시 위험물 알림 발송"""
    if status in ("alarm", "smoke", "fire") or event_type == "smoke_detected":
        alert_type = "fire" if status == "fire" else "smoke"
        title = "🔥 화재 감지" if alert_type == "fire" else "🚨 연기 감지"
        msg = (f"장치: {device_name or device_key}\n"
               f"위치: {location or '알 수 없음'}\n"
               f"상태: {status}\n"
               f"즉시 대피 및 초기진화 조치를 취하세요!")
        threading.Thread(
            target=_dispatch_hazmat_alert,
            args=(alert_type, title, msg),
            kwargs={"camera_url": None},
            daemon=True
        ).start()


# ── 라우트 ───────────────────────────────────────────────────

@hazmat_bp.route("/")
@_login_required
def hazmat_main():
    conn = _conn(); cur = conn.cursor()
    today = date.today()
    cur.execute("""SELECT id, name, category, quantity, unit, location,
                          storage_temp_max, storage_temp_min, storage_humi_max,
                          sensor_device_id, inspect_cycle_days, last_inspected, next_inspect, is_active, note
                   FROM hazmat_items ORDER BY is_active DESC, name""")
    rows = cur.fetchall()
    items = []
    for r in rows:
        ni = r[12]
        if ni:
            days_left = (ni - today).days
            if days_left < 0:
                status_class = "danger"
                status_text = f"기한 초과 ({abs(days_left)}일)"
            elif days_left <= 7:
                status_class = "warning"
                status_text = f"D-{days_left}"
            else:
                status_class = "success"
                status_text = f"D-{days_left}"
        else:
            status_class = "secondary"
            status_text = "미설정"
        items.append({
            "id": r[0], "name": r[1], "category": r[2] or "",
            "quantity": r[3], "unit": r[4] or "", "location": r[5] or "",
            "temp_max": r[6], "temp_min": r[7], "humi_max": r[8],
            "device_id": r[9] or "", "cycle": r[10],
            "last_inspected": r[11].strftime("%Y-%m-%d") if r[11] else "",
            "next_inspect": r[12].strftime("%Y-%m-%d") if r[12] else "",
            "is_active": bool(r[13]), "note": r[14] or "",
            "status_class": status_class, "status_text": status_text,
        })
    # 최근 알림 이력
    cur.execute("""SELECT alert_type, title, sent_via, created_at
                   FROM hazmat_alerts ORDER BY created_at DESC LIMIT 10""")
    alerts = [{"type": r[0], "title": r[1], "via": r[2] or "",
               "created_at": r[3].strftime("%Y-%m-%d %H:%M")} for r in cur.fetchall()]
    # MQTT 기기 목록
    cur.execute("SELECT DISTINCT device_id FROM mes_env_log ORDER BY device_id")
    devices = [r[0] for r in cur.fetchall()]
    conn.close()
    return render_template("hazmat.html", items=items, alerts=alerts,
                           devices=devices, active_page="hazmat")


@hazmat_bp.route("/recipients")
@_admin_required
def hazmat_recipients():
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT id,name,phone,email,tg_chat_id,is_active FROM hazmat_recipients ORDER BY name")
    recipients = [{"id":r[0],"name":r[1],"phone":r[2] or "","email":r[3] or "",
                   "tg_chat_id":r[4] or "","is_active":bool(r[5])} for r in cur.fetchall()]
    conn.close()
    return render_template("hazmat_recipients.html", recipients=recipients, active_page="hazmat")


@hazmat_bp.route("/alerts")
@_login_required
def hazmat_alerts():
    conn = _conn(); cur = conn.cursor()
    cur.execute("""SELECT ha.id, hi.name, ha.alert_type, ha.title, ha.message, ha.sent_via, ha.created_at
                   FROM hazmat_alerts ha
                   LEFT JOIN hazmat_items hi ON hi.id=ha.hazmat_id
                   ORDER BY ha.created_at DESC LIMIT 200""")
    alerts = [{"id":r[0],"item":r[1] or "-","type":r[2],"title":r[3],
               "message":r[4] or "","via":r[5] or "",
               "created_at":r[6].strftime("%Y-%m-%d %H:%M")} for r in cur.fetchall()]
    conn.close()
    return render_template("hazmat_alerts.html", alerts=alerts, active_page="hazmat")


# ── API ──────────────────────────────────────────────────────

@hazmat_bp.route("/api/item/save", methods=["POST"])
@_admin_required
def api_item_save():
    try:
        d = request.get_json(force=True)
        iid = d.get("id")
        name = d["name"].strip()
        category = d.get("category", "")
        quantity = float(d["quantity"]) if d.get("quantity") not in (None, "") else None
        unit = d.get("unit", "")
        location = d.get("location", "")
        temp_max = float(d["storage_temp_max"]) if d.get("storage_temp_max") not in (None, "") else None
        temp_min = float(d["storage_temp_min"]) if d.get("storage_temp_min") not in (None, "") else None
        humi_max = float(d["storage_humi_max"]) if d.get("storage_humi_max") not in (None, "") else None
        dev_id = d.get("sensor_device_id", "")
        cam_url = d.get("camera_url", "")
        cycle = int(d.get("inspect_cycle_days", 30))
        last_ins = d.get("last_inspected") or None
        note = d.get("note", "")
        # next_inspect 자동 계산
        next_ins = None
        if last_ins:
            from datetime import datetime as _dt
            li = _dt.strptime(last_ins, "%Y-%m-%d").date()
            next_ins = (li + timedelta(days=cycle)).strftime("%Y-%m-%d")

        conn = _conn(); cur = conn.cursor()
        if iid:
            cur.execute("""UPDATE hazmat_items SET name=%s,category=%s,quantity=%s,unit=%s,
                           location=%s,storage_temp_max=%s,storage_temp_min=%s,storage_humi_max=%s,
                           sensor_device_id=%s,camera_url=%s,inspect_cycle_days=%s,
                           last_inspected=%s,next_inspect=%s,note=%s WHERE id=%s""",
                        (name,category,quantity,unit,location,temp_max,temp_min,humi_max,
                         dev_id,cam_url,cycle,last_ins,next_ins,note,iid))
        else:
            cur.execute("""INSERT INTO hazmat_items
                           (name,category,quantity,unit,location,storage_temp_max,storage_temp_min,
                            storage_humi_max,sensor_device_id,camera_url,inspect_cycle_days,
                            last_inspected,next_inspect,note)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (name,category,quantity,unit,location,temp_max,temp_min,humi_max,
                         dev_id,cam_url,cycle,last_ins,next_ins,note))
            iid = cur.lastrowid
        conn.commit(); conn.close()
        return jsonify(ok=True, id=iid, next_inspect=next_ins)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@hazmat_bp.route("/api/item/delete", methods=["POST"])
@_admin_required
def api_item_delete():
    try:
        d = request.get_json(force=True)
        conn = _conn(); cur = conn.cursor()
        cur.execute("DELETE FROM hazmat_items WHERE id=%s", (d["id"],))
        conn.commit(); conn.close()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@hazmat_bp.route("/api/item/inspect", methods=["POST"])
@_admin_required
def api_item_inspect():
    """점검 완료 처리 — last_inspected 갱신 + next_inspect 재계산"""
    try:
        d = request.get_json(force=True)
        conn = _conn(); cur = conn.cursor()
        cur.execute("SELECT inspect_cycle_days FROM hazmat_items WHERE id=%s", (d["id"],))
        row = cur.fetchone()
        if not row:
            conn.close(); return jsonify(ok=False, error="항목 없음"), 404
        cycle = row[0] or 30
        today = date.today()
        next_ins = today + timedelta(days=cycle)
        cur.execute("""UPDATE hazmat_items SET last_inspected=%s, next_inspect=%s,
                       last_alert_sent=NULL WHERE id=%s""",
                    (today, next_ins, d["id"]))
        conn.commit(); conn.close()
        return jsonify(ok=True, next_inspect=next_ins.strftime("%Y-%m-%d"))
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@hazmat_bp.route("/api/recipient/save", methods=["POST"])
@_admin_required
def api_recipient_save():
    try:
        d = request.get_json(force=True)
        rid = d.get("id")
        conn = _conn(); cur = conn.cursor()
        if rid:
            cur.execute("""UPDATE hazmat_recipients SET name=%s,phone=%s,email=%s,tg_chat_id=%s
                           WHERE id=%s""",
                        (d["name"],d.get("phone",""),d.get("email",""),d.get("tg_chat_id",""),rid))
        else:
            cur.execute("""INSERT INTO hazmat_recipients (name,phone,email,tg_chat_id)
                           VALUES (%s,%s,%s,%s)""",
                        (d["name"],d.get("phone",""),d.get("email",""),d.get("tg_chat_id","")))
            rid = cur.lastrowid
        conn.commit(); conn.close()
        return jsonify(ok=True, id=rid)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@hazmat_bp.route("/api/recipient/toggle", methods=["POST"])
@_admin_required
def api_recipient_toggle():
    try:
        d = request.get_json(force=True)
        conn = _conn(); cur = conn.cursor()
        cur.execute("UPDATE hazmat_recipients SET is_active=1-is_active WHERE id=%s", (d["id"],))
        conn.commit(); conn.close()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@hazmat_bp.route("/api/recipient/delete", methods=["POST"])
@_admin_required
def api_recipient_delete():
    try:
        d = request.get_json(force=True)
        conn = _conn(); cur = conn.cursor()
        cur.execute("DELETE FROM hazmat_recipients WHERE id=%s", (d["id"],))
        conn.commit(); conn.close()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@hazmat_bp.route("/api/test_alert", methods=["POST"])
@_admin_required
def api_test_alert():
    """수동 테스트 알림 발송"""
    try:
        d = request.get_json(force=True)
        item_id = d.get("item_id")
        cam_url = None
        item_name = "테스트"
        if item_id:
            conn = _conn(); cur = conn.cursor()
            cur.execute("SELECT name, camera_url FROM hazmat_items WHERE id=%s", (item_id,))
            row = cur.fetchone()
            conn.close()
            if row:
                item_name, cam_url = row[0], row[1]
        threading.Thread(
            target=_dispatch_hazmat_alert,
            args=("manual", f"🔔 테스트 알림 — {item_name}",
                  f"안전관리 시스템 테스트 알림입니다.\n발송 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"),
            kwargs={"hazmat_id": item_id, "camera_url": cam_url},
            daemon=True
        ).start()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500
