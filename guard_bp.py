"""경비일지 Blueprint — 웹 조회/인쇄 + APK 연동 API

- 로그인은 기존 app_users / _hash_pw 를 그대로 사용 (태인관리시스템 계정 공유)
- 웹은 세션 인증, APK는 app_api_tokens 기반 Bearer 토큰 인증
- 앱이 오프라인에서 작성한 일지는 스냅샷 단위로 멱등 업로드 (/api/guard/sync)
"""
import os
import hashlib
import secrets
import uuid
from datetime import datetime, date, timedelta
from functools import wraps

import pymysql
from flask import (Blueprint, render_template, request, jsonify, session,
                   redirect, flash, g, send_from_directory, abort)
from werkzeug.utils import secure_filename

guard_bp = Blueprint("guard", __name__)

# 순찰 회차 (원본 양식 기준 22:00 / 04:00 2회)
GUARD_ROUNDS = ["22:00", "04:00"]

# 점검 결과 값
RESULT_OK = "정상"
RESULT_NG = "이상"
RESULT_NONE = "미점검"
VALID_RESULTS = (RESULT_OK, RESULT_NG, RESULT_NONE)

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

# 기본 점검장소 (최초 1회 시드)
DEFAULT_POINTS = [
    "3F 사무실, 그림 창고",
    "3F 전기, 전자 라인",
    "2F 세척실",
    "2F 휴게실, 화장실",
    "1F 휴게실, 화장실",
    "1F 자재창고, 가공실",
    "1F 콤프레샤 실",
    "지하 식당, 강당",
    "지하 박스 창고",
    "잔디밭 소방펌프실",
]

GUARD_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", "guard")
ALLOWED_PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_PHOTO_BYTES = 10 * 1024 * 1024

# OTA — APK 배포 (nginx client_max_body_size 50M 이내로 제한)
GUARD_APK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", "guard_apk")
MAX_APK_BYTES = 40 * 1024 * 1024

TOKEN_TTL_DAYS = 90


# ── 공통 헬퍼 ────────────────────────────────────────────────
def _conn():
    from app_maria import MARIA
    return pymysql.connect(**MARIA)


def _hash_pw(pw):
    """app_maria._hash_pw 와 동일한 해시 (계정 DB 공유)"""
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


def _login_required(f):
    @wraps(f)
    def deco(*a, **kw):
        if not session.get("user_id"):
            return redirect("/login")
        return f(*a, **kw)
    return deco


def _has_perm(perm):
    if session.get("role") == "admin":
        return True
    perms = session.get("permissions", "") or ""
    return perm in [p.strip() for p in perms.split(",") if p.strip()]


def _guard_required(f):
    """경비일지 조회 권한 (admin 또는 permissions 에 guard 포함)"""
    @wraps(f)
    def deco(*a, **kw):
        if not session.get("user_id"):
            return redirect("/login")
        if not _has_perm("guard"):
            flash("경비일지 조회 권한이 없습니다.", "danger")
            return redirect("/")
        return f(*a, **kw)
    return deco


def _admin_required(f):
    @wraps(f)
    def deco(*a, **kw):
        if not session.get("user_id"):
            return redirect("/login")
        if session.get("role") != "admin":
            flash("관리자 전용입니다.", "danger")
            return redirect("/")
        return f(*a, **kw)
    return deco


def _api_auth(f):
    """APK용 Bearer 토큰 인증. 세션이 있으면 세션도 허용(웹 fetch 겸용)."""
    @wraps(f)
    def deco(*a, **kw):
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if token:
            conn = _conn(); cur = conn.cursor()
            cur.execute("""
                SELECT t.id, t.user_id, u.login_id, u.name, u.e_id, u.role, u.permissions
                  FROM app_api_tokens t JOIN app_users u ON u.id = t.user_id
                 WHERE t.token=%s AND t.revoked_at IS NULL
                   AND (t.expires_at IS NULL OR t.expires_at > NOW())
            """, (token,))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE app_api_tokens SET last_used_at=NOW() WHERE id=%s", (row[0],))
                conn.commit(); conn.close()
                g.guard_user = {"user_id": row[1], "login_id": row[2], "name": row[3],
                                "e_id": row[4], "role": row[5], "permissions": row[6] or ""}
                return f(*a, **kw)
            conn.close()
            return jsonify(ok=False, error="토큰이 만료되었거나 유효하지 않습니다."), 401
        if session.get("user_id"):
            g.guard_user = {"user_id": session["user_id"], "login_id": session.get("login_id"),
                            "name": session.get("user_name"), "e_id": session.get("e_id"),
                            "role": session.get("role"), "permissions": session.get("permissions", "")}
            return f(*a, **kw)
        return jsonify(ok=False, error="로그인이 필요합니다."), 401
    return deco


def _is_admin_user():
    u = getattr(g, "guard_user", None) or {}
    return u.get("role") == "admin"


def _fmt_dt(v):
    return v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else (v or None)


def _parse_dt(v):
    """앱이 보낸 시각 문자열 → datetime (실패 시 None)"""
    if not v:
        return None
    s = str(v).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            continue
    return None


def _parse_date(v):
    if not v:
        return None
    try:
        return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _weekday_kr(d):
    return WEEKDAY_KR[d.weekday()] if isinstance(d, date) else ""


# ── DB 초기화 ────────────────────────────────────────────────
def init_guard_db(app):
    with app.app_context():
        conn = _conn(); cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS `guard_points` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `seq` INT DEFAULT 0,
                `name` VARCHAR(100) NOT NULL,
                `is_active` TINYINT(1) DEFAULT 1,
                `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS `guard_logs` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `log_date` DATE NOT NULL,
                `weekday` VARCHAR(3) DEFAULT '',
                `guard_e_id` INT NOT NULL,
                `guard_name` VARCHAR(50) DEFAULT '',
                `instructions` TEXT,
                `remarks` TEXT,
                `phone_memo` VARCHAR(255) DEFAULT '',
                `last_leaver` VARCHAR(255) DEFAULT '',
                `night_worker` VARCHAR(255) DEFAULT '',
                `water_meter` VARCHAR(100) DEFAULT '',
                `waste_request` VARCHAR(255) DEFAULT '',
                `status` VARCHAR(12) DEFAULT 'draft',
                `submitted_at` DATETIME NULL,
                `created_by` INT NULL,
                `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY `uq_guard_log` (`log_date`, `guard_e_id`),
                KEY `idx_guard_date` (`log_date`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS `guard_patrol` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `log_id` INT NOT NULL,
                `point_id` INT NOT NULL,
                `round_key` VARCHAR(8) NOT NULL,
                `result` VARCHAR(10) DEFAULT '미점검',
                `note` VARCHAR(255) DEFAULT '',
                `checked_at` DATETIME NULL,
                UNIQUE KEY `uq_guard_patrol` (`log_id`, `point_id`, `round_key`),
                KEY `idx_patrol_log` (`log_id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS `guard_visitors` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `log_id` INT NOT NULL,
                `block` VARCHAR(10) DEFAULT 'visitor',
                `seq` INT DEFAULT 0,
                `in_time` VARCHAR(5) DEFAULT '',
                `out_time` VARCHAR(5) DEFAULT '',
                `company` VARCHAR(60) DEFAULT '',
                `name` VARCHAR(40) DEFAULT '',
                `purpose` VARCHAR(120) DEFAULT '',
                `car_no` VARCHAR(30) DEFAULT '',
                `note` VARCHAR(120) DEFAULT '',
                KEY `idx_visitor_log` (`log_id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS `guard_photos` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `log_id` INT NOT NULL,
                `filename` VARCHAR(160) NOT NULL,
                `orig_name` VARCHAR(200) DEFAULT '',
                `filesize` INT DEFAULT 0,
                `uploaded_by` INT NULL,
                `uploaded_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
                KEY `idx_photo_log` (`log_id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS `app_api_tokens` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `token` VARCHAR(64) NOT NULL,
                `user_id` INT NOT NULL,
                `device` VARCHAR(120) DEFAULT '',
                `app` VARCHAR(30) DEFAULT 'guard',
                `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
                `last_used_at` DATETIME NULL,
                `expires_at` DATETIME NULL,
                `revoked_at` DATETIME NULL,
                UNIQUE KEY `uq_api_token` (`token`),
                KEY `idx_token_user` (`user_id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS `guard_instructions` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `target_date` DATE NOT NULL,
                `content` TEXT,
                `created_by` INT NULL,
                `created_by_name` VARCHAR(50) DEFAULT '',
                `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY `uq_guard_instruction` (`target_date`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS `guard_app_releases` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `version_code` INT NOT NULL,
                `version_name` VARCHAR(30) NOT NULL,
                `filename` VARCHAR(160) NOT NULL,
                `filesize` INT DEFAULT 0,
                `sha256` VARCHAR(64) DEFAULT '',
                `release_note` TEXT,
                `min_version_code` INT DEFAULT 0,
                `is_active` TINYINT(1) DEFAULT 1,
                `uploaded_by` INT NULL,
                `uploaded_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY `uq_app_version` (`version_code`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # 점검장소 최초 시드
        cur.execute("SELECT COUNT(*) FROM guard_points")
        if cur.fetchone()[0] == 0:
            for i, nm in enumerate(DEFAULT_POINTS, start=1):
                cur.execute("INSERT INTO guard_points (seq, name, is_active) VALUES (%s,%s,1)", (i, nm))

        conn.commit(); conn.close()
        os.makedirs(GUARD_UPLOAD_DIR, exist_ok=True)
        os.makedirs(GUARD_APK_DIR, exist_ok=True)


# ── 조회 헬퍼 ────────────────────────────────────────────────
def _load_points(cur, active_only=True):
    sql = "SELECT id, seq, name, is_active FROM guard_points"
    if active_only:
        sql += " WHERE is_active=1"
    sql += " ORDER BY seq, id"
    cur.execute(sql)
    return [{"id": r[0], "seq": r[1], "name": r[2], "is_active": int(r[3])} for r in cur.fetchall()]


def _instruction_for(cur, d):
    """해당 날짜에 관리자가 등록해 둔 지시사항 (없으면 None)"""
    cur.execute("""
        SELECT content, created_by_name, updated_at
          FROM guard_instructions WHERE target_date=%s
    """, (d,))
    r = cur.fetchone()
    if not r or not (r[0] or "").strip():
        return None
    return {"content": r[0], "author": r[1] or "", "updated_at": _fmt_dt(r[2])}


WATER_SRC = "경비일지"


def _to_reading(raw):
    """자유 입력 문자열 → 검침값. 숫자가 아니거나 음수면 None."""
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


def _write_water_meter(cur, d, reading, guard_name):
    """관리자가 통합관제에서 직접 넣거나 고친 값은 덮어쓰지 않는다
    (created_by 가 '경비일지…' 인 행, 즉 이 경로로 들어온 값만 갱신)"""
    cur.execute("""
        INSERT INTO water_meter (read_date, reading, memo, created_by)
        VALUES (%s,%s,'경비일지 자동등록',%s)
        ON DUPLICATE KEY UPDATE
            reading = IF(created_by LIKE %s, VALUES(reading), reading),
            memo    = IF(created_by LIKE %s, VALUES(memo),    memo)
    """, (d, reading,
          ("%s %s" % (WATER_SRC, guard_name)).strip()[:30],  # created_by 는 VARCHAR(30), STRICT 모드라 초과 시 저장 전체가 실패한다
          WATER_SRC + "%", WATER_SRC + "%"))


def _reclaim_water_meter(cur, d):
    """그날 남아 있는 일지에서 지침을 다시 찾아 반영한다.

    하루에 근무자별로 여러 건이 있을 수 있다 (guard_logs 는 (log_date, guard_e_id) 유니크).
    한 사람이 상수도를 비운 채 저장했다고 해서 다른 근무자가 적어 둔 검침까지 지우면 안 된다.
    숫자 지침이 하나도 없을 때만 이 경로로 등록됐던 검침을 거둬들인다.
    """
    cur.execute("SELECT water_meter, guard_name FROM guard_logs WHERE log_date=%s ORDER BY id", (d,))
    for raw, name in cur.fetchall():
        reading = _to_reading(raw)
        if reading is not None:
            _write_water_meter(cur, d, reading, name or "")
            return
    cur.execute("DELETE FROM water_meter WHERE read_date=%s AND created_by LIKE %s",
                (d, WATER_SRC + "%"))


def _sync_water_meter(cur, d, raw, guard_name):
    """경비일지의 상수도 지침을 통합관제 water_meter 테이블에 반영한다.

    숫자로 읽히지 않으면(값을 지웠거나 옛 일지의 자유 입력이면) 그날 다른 일지의
    지침으로 되돌리고, 그것도 없을 때만 거둬들인다.
    """
    reading = _to_reading(raw)
    if reading is None:
        _reclaim_water_meter(cur, d)
        return
    _write_water_meter(cur, d, reading, guard_name)


def _load_log(cur, log_id):
    cur.execute("""
        SELECT id, log_date, weekday, guard_e_id, guard_name, instructions, remarks,
               phone_memo, last_leaver, night_worker, water_meter, waste_request,
               status, submitted_at, created_by, created_at, updated_at
          FROM guard_logs WHERE id=%s
    """, (log_id,))
    r = cur.fetchone()
    if not r:
        return None
    log = {
        "id": r[0], "log_date": r[1].strftime("%Y-%m-%d") if r[1] else None, "weekday": r[2],
        "guard_e_id": r[3], "guard_name": r[4], "instructions": r[5] or "", "remarks": r[6] or "",
        "phone_memo": r[7] or "", "last_leaver": r[8] or "", "night_worker": r[9] or "",
        "water_meter": r[10] or "", "waste_request": r[11] or "", "status": r[12],
        "submitted_at": _fmt_dt(r[13]), "created_by": r[14],
        "created_at": _fmt_dt(r[15]), "updated_at": _fmt_dt(r[16]),
    }
    cur.execute("""
        SELECT point_id, round_key, result, note, checked_at
          FROM guard_patrol WHERE log_id=%s
    """, (log_id,))
    log["patrol"] = [{"point_id": p[0], "round_key": p[1], "result": p[2],
                      "note": p[3] or "", "checked_at": _fmt_dt(p[4])} for p in cur.fetchall()]
    cur.execute("""
        SELECT id, block, seq, in_time, out_time, company, name, purpose, car_no, note
          FROM guard_visitors WHERE log_id=%s ORDER BY block, seq, id
    """, (log_id,))
    log["visitors"] = [{"id": v[0], "block": v[1], "seq": v[2], "in_time": v[3] or "",
                        "out_time": v[4] or "", "company": v[5] or "", "name": v[6] or "",
                        "purpose": v[7] or "", "car_no": v[8] or "", "note": v[9] or ""}
                       for v in cur.fetchall()]
    cur.execute("""
        SELECT id, filename, orig_name, filesize, uploaded_at
          FROM guard_photos WHERE log_id=%s ORDER BY id
    """, (log_id,))
    log["photos"] = [{"id": p[0], "filename": p[1], "orig_name": p[2] or "",
                      "filesize": p[3], "uploaded_at": _fmt_dt(p[4]),
                      "url": "/guard/photo/%d" % p[0]} for p in cur.fetchall()]
    return log


def _delete_log(cur, log_id):
    """일지와 딸린 기록을 모두 지운다. 삭제된 사진 파일명 목록을 반환."""
    cur.execute("SELECT filename FROM guard_photos WHERE log_id=%s", (log_id,))
    files = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT log_date FROM guard_logs WHERE id=%s", (log_id,))
    row = cur.fetchone()

    for t in ("guard_patrol", "guard_visitors", "guard_photos"):
        cur.execute("DELETE FROM `%s` WHERE log_id=%%s" % t, (log_id,))
    cur.execute("DELETE FROM guard_logs WHERE id=%s", (log_id,))

    # 지운 일지의 지침이 통합관제에 남아 있을 수 있다.
    # 그날 남은 일지 기준으로 다시 맞추고, 남은 게 없으면 거둬들인다.
    if row:
        _reclaim_water_meter(cur, row[0])
    return files


def _save_snapshot(cur, payload, actor, force=False, allow_any=False):
    """일지 스냅샷 저장 (upsert). (log_id, conflict) 반환.

    allow_any=True 는 웹 관리 화면 전용 — 남의 일지도 수정할 수 있다.
    """
    d = _parse_date(payload.get("log_date"))
    if not d:
        raise ValueError("log_date 형식이 올바르지 않습니다. (YYYY-MM-DD)")

    e_id = payload.get("guard_e_id") or actor.get("e_id")
    if not e_id:
        raise ValueError("근무자 정보(guard_e_id)가 없습니다.")
    e_id = int(e_id)

    # 본인 외 근무자 일지는 관리자만 작성/수정 (웹 관리 화면은 allow_any 로 예외)
    if not allow_any and e_id != (actor.get("e_id") or -1) and actor.get("role") != "admin":
        raise PermissionError("본인 일지만 작성할 수 있습니다.")

    guard_name = (payload.get("guard_name") or "").strip()
    if not guard_name:
        cur.execute("SELECT name FROM tuser WHERE id=%s", (e_id,))
        rn = cur.fetchone()
        guard_name = rn[0] if rn else (actor.get("name") or "")

    cur.execute("SELECT id, status, updated_at FROM guard_logs WHERE log_date=%s AND guard_e_id=%s", (d, e_id))
    exist = cur.fetchone()

    # 낙관적 잠금 — 앱이 마지막으로 받아간 서버 시각보다 서버가 더 최신이면 충돌
    if exist and not force:
        base = _parse_dt(payload.get("base_updated_at"))
        if base and exist[2] and exist[2] > base + timedelta(seconds=1):
            return exist[0], True

    fields = dict(
        weekday=payload.get("weekday") or _weekday_kr(d),
        guard_name=guard_name,
        instructions=payload.get("instructions") or "",
        remarks=payload.get("remarks") or "",
        phone_memo=payload.get("phone_memo") or "",
        last_leaver=payload.get("last_leaver") or "",
        night_worker=payload.get("night_worker") or "",
        water_meter=payload.get("water_meter") or "",
        waste_request=payload.get("waste_request") or "",
    )

    # 관리자가 등록해 둔 지시사항이 있고 일지에 아직 내용이 없으면 자동으로 채운다
    if not (fields["instructions"] or "").strip():
        ins = _instruction_for(cur, d)
        if ins:
            fields["instructions"] = ins["content"]

    if exist:
        log_id = exist[0]
        cur.execute("""
            UPDATE guard_logs SET weekday=%s, guard_name=%s, instructions=%s, remarks=%s,
                   phone_memo=%s, last_leaver=%s, night_worker=%s, water_meter=%s, waste_request=%s
             WHERE id=%s
        """, tuple(fields.values()) + (log_id,))
    else:
        cur.execute("""
            INSERT INTO guard_logs (log_date, guard_e_id, weekday, guard_name, instructions, remarks,
                                    phone_memo, last_leaver, night_worker, water_meter, waste_request,
                                    status, created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft',%s)
        """, (d, e_id) + tuple(fields.values()) + (actor.get("user_id"),))
        log_id = cur.lastrowid

    # 순찰 결과 — 전송된 항목만 upsert (미전송 항목은 기존값 유지)
    if isinstance(payload.get("patrol"), list):
        for p in payload["patrol"]:
            try:
                pid = int(p.get("point_id"))
            except (TypeError, ValueError):
                continue
            rk = str(p.get("round_key") or "").strip()
            if not rk:
                continue
            res = p.get("result") or RESULT_NONE
            if res not in VALID_RESULTS:
                res = RESULT_NONE
            note = (p.get("note") or "").strip()[:255]
            if res == RESULT_NG and not note:
                cur.execute("SELECT name FROM guard_points WHERE id=%s", (pid,))
                pn = cur.fetchone()
                raise ValueError("%s %s — '이상' 항목은 비고를 입력해야 합니다."
                                 % (rk, pn[0] if pn else "점검장소"))
            cur.execute("""
                INSERT INTO guard_patrol (log_id, point_id, round_key, result, note, checked_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE result=VALUES(result), note=VALUES(note),
                                        checked_at=VALUES(checked_at)
            """, (log_id, pid, rk, res, note, _parse_dt(p.get("checked_at"))))

    # 상수도 검침 — 통합관제(water_meter)에 그대로 반영
    _sync_water_meter(cur, d, fields["water_meter"], guard_name)

    # 출입기록 — 스냅샷 전체 교체 (멱등)
    if isinstance(payload.get("visitors"), list):
        cur.execute("DELETE FROM guard_visitors WHERE log_id=%s", (log_id,))
        for i, v in enumerate(payload["visitors"], start=1):
            blk = "staff" if str(v.get("block")) == "staff" else "visitor"
            cur.execute("""
                INSERT INTO guard_visitors (log_id, block, seq, in_time, out_time, company,
                                            name, purpose, car_no, note)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (log_id, blk, v.get("seq") or i,
                  (v.get("in_time") or "")[:5], (v.get("out_time") or "")[:5],
                  (v.get("company") or "")[:60], (v.get("name") or "")[:40],
                  (v.get("purpose") or "")[:120], (v.get("car_no") or "")[:30],
                  (v.get("note") or "")[:120]))

    return log_id, False


# ════════════════════════════════════════════════════════════
# API — 인증
# ════════════════════════════════════════════════════════════
@guard_bp.route("/api/guard/auth/login", methods=["POST"])
def api_guard_login():
    data = request.get_json(silent=True) or {}
    login_id = str(data.get("login_id", "")).strip()
    password = str(data.get("password", "")).strip()
    device = str(data.get("device", ""))[:120]
    if not login_id or not password:
        return jsonify(ok=False, error="아이디와 비밀번호를 입력하세요."), 400

    conn = _conn(); cur = conn.cursor()
    cur.execute("""
        SELECT id, login_id, password, name, e_id, role, must_change_pw, `permissions`
          FROM app_users WHERE login_id=%s
    """, (login_id,))
    row = cur.fetchone()
    if not row or row[2] != _hash_pw(password):
        conn.close()
        return jsonify(ok=False, error="아이디 또는 비밀번호가 올바르지 않습니다."), 401

    token = secrets.token_urlsafe(40)[:64]
    cur.execute("""
        INSERT INTO app_api_tokens (token, user_id, device, app, expires_at)
        VALUES (%s,%s,%s,'guard', DATE_ADD(NOW(), INTERVAL %s DAY))
    """, (token, row[0], device, TOKEN_TTL_DAYS))
    cur.execute("UPDATE app_users SET last_login=NOW() WHERE id=%s", (row[0],))
    conn.commit(); conn.close()

    return jsonify(ok=True, token=token, user={
        "user_id": row[0], "login_id": row[1], "name": row[3], "e_id": row[4],
        "role": row[5], "must_change_pw": int(row[6] or 0), "permissions": row[7] or "",
    })


@guard_bp.route("/api/guard/auth/logout", methods=["POST"])
def api_guard_logout():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        conn = _conn(); cur = conn.cursor()
        cur.execute("UPDATE app_api_tokens SET revoked_at=NOW() WHERE token=%s AND revoked_at IS NULL",
                    (auth[7:].strip(),))
        conn.commit(); conn.close()
    return jsonify(ok=True)


@guard_bp.route("/api/guard/me")
@_api_auth
def api_guard_me():
    return jsonify(ok=True, user=g.guard_user, rounds=GUARD_ROUNDS)


@guard_bp.route("/api/guard/auth/change_password", methods=["POST"])
@_api_auth
def api_guard_change_pw():
    data = request.get_json(silent=True) or {}
    cur_pw = str(data.get("current_password", ""))
    new_pw = str(data.get("new_password", ""))
    if len(new_pw) < 4:
        return jsonify(ok=False, error="새 비밀번호는 4자리 이상이어야 합니다."), 400
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT password FROM app_users WHERE id=%s", (g.guard_user["user_id"],))
    row = cur.fetchone()
    if not row or row[0] != _hash_pw(cur_pw):
        conn.close()
        return jsonify(ok=False, error="현재 비밀번호가 올바르지 않습니다."), 400
    cur.execute("UPDATE app_users SET password=%s, must_change_pw=0 WHERE id=%s",
                (_hash_pw(new_pw), g.guard_user["user_id"]))
    conn.commit(); conn.close()
    return jsonify(ok=True)


# ════════════════════════════════════════════════════════════
# API — 마스터 / 일지
# ════════════════════════════════════════════════════════════
@guard_bp.route("/api/guard/points")
@_api_auth
def api_guard_points():
    conn = _conn(); cur = conn.cursor()
    pts = _load_points(cur)
    conn.close()
    return jsonify(ok=True, points=pts, rounds=GUARD_ROUNDS)


@guard_bp.route("/api/guard/instructions")
@_api_auth
def api_guard_instructions():
    """앱이 일지를 열 때 해당 날짜의 관리자 지시사항을 받아간다."""
    d = _parse_date(request.args.get("date")) or date.today()
    conn = _conn(); cur = conn.cursor()
    ins = _instruction_for(cur, d)
    conn.close()
    return jsonify(ok=True, date=d.strftime("%Y-%m-%d"), instruction=ins)


@guard_bp.route("/api/guard/logs")
@_api_auth
def api_guard_logs():
    d_from = _parse_date(request.args.get("from")) or (date.today() - timedelta(days=30))
    d_to = _parse_date(request.args.get("to")) or date.today()
    conn = _conn(); cur = conn.cursor()

    where = "WHERE l.log_date BETWEEN %s AND %s"
    params = [d_from, d_to]
    if not _is_admin_user() and not ("guard" in (g.guard_user.get("permissions") or "")):
        where += " AND l.guard_e_id=%s"
        params.append(g.guard_user.get("e_id") or -1)

    cur.execute("""
        SELECT l.id, l.log_date, l.weekday, l.guard_e_id, l.guard_name, l.status,
               l.submitted_at, l.updated_at,
               (SELECT COUNT(*) FROM guard_patrol p WHERE p.log_id=l.id AND p.result<>'미점검'),
               (SELECT COUNT(*) FROM guard_patrol p WHERE p.log_id=l.id AND p.result='이상'),
               (SELECT COUNT(*) FROM guard_visitors v WHERE v.log_id=l.id)
          FROM guard_logs l %s
         ORDER BY l.log_date DESC, l.id DESC
    """ % where, params)
    rows = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM guard_points WHERE is_active=1")
    total_slots = (cur.fetchone()[0] or 0) * len(GUARD_ROUNDS)
    conn.close()

    logs = [{"id": r[0], "log_date": r[1].strftime("%Y-%m-%d"), "weekday": r[2],
             "guard_e_id": r[3], "guard_name": r[4], "status": r[5],
             "submitted_at": _fmt_dt(r[6]), "updated_at": _fmt_dt(r[7]),
             "patrol_done": r[8], "patrol_total": total_slots,
             "abnormal_cnt": r[9], "visitor_cnt": r[10]} for r in rows]
    return jsonify(ok=True, logs=logs)


@guard_bp.route("/api/guard/logs/<int:log_id>")
@_api_auth
def api_guard_log_detail(log_id):
    conn = _conn(); cur = conn.cursor()
    log = _load_log(cur, log_id)
    if not log:
        conn.close()
        return jsonify(ok=False, error="일지를 찾을 수 없습니다."), 404
    if log["guard_e_id"] != g.guard_user.get("e_id") and not _is_admin_user() \
            and "guard" not in (g.guard_user.get("permissions") or ""):
        conn.close()
        return jsonify(ok=False, error="조회 권한이 없습니다."), 403
    log["points"] = _load_points(cur)
    conn.close()
    return jsonify(ok=True, log=log, rounds=GUARD_ROUNDS)


@guard_bp.route("/api/guard/logs", methods=["POST"])
@_api_auth
def api_guard_log_save():
    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force"))
    conn = _conn(); cur = conn.cursor()
    try:
        log_id, conflict = _save_snapshot(cur, payload, g.guard_user, force=force)
    except PermissionError as e:
        conn.rollback(); conn.close()
        return jsonify(ok=False, error=str(e)), 403
    except ValueError as e:
        conn.rollback(); conn.close()
        return jsonify(ok=False, error=str(e)), 400
    if conflict:
        log = _load_log(cur, log_id)
        conn.close()
        return jsonify(ok=False, error="서버에 더 최신 내용이 있습니다.", conflict=True, log=log), 409
    conn.commit()
    log = _load_log(cur, log_id)
    conn.close()
    return jsonify(ok=True, log=log)


@guard_bp.route("/api/guard/logs/<int:log_id>/submit", methods=["POST"])
@_api_auth
def api_guard_log_submit(log_id):
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT guard_e_id, status FROM guard_logs WHERE id=%s", (log_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify(ok=False, error="일지를 찾을 수 없습니다."), 404
    if row[0] != g.guard_user.get("e_id") and not _is_admin_user():
        conn.close()
        return jsonify(ok=False, error="본인 일지만 제출할 수 있습니다."), 403
    cur.execute("UPDATE guard_logs SET status='submitted', submitted_at=NOW() WHERE id=%s", (log_id,))
    conn.commit()
    log = _load_log(cur, log_id)
    conn.close()
    return jsonify(ok=True, log=log)


@guard_bp.route("/api/guard/sync", methods=["POST"])
@_api_auth
def api_guard_sync():
    """오프라인 큐 일괄 업로드. 항목별 성공/충돌을 개별 보고."""
    data = request.get_json(silent=True) or {}
    items = data.get("logs") or []
    if not isinstance(items, list):
        return jsonify(ok=False, error="logs 배열이 필요합니다."), 400

    results = []
    conn = _conn(); cur = conn.cursor()
    for item in items[:100]:
        client_id = item.get("client_id")
        try:
            log_id, conflict = _save_snapshot(cur, item, g.guard_user, force=bool(item.get("force")))
            if conflict:
                conn.rollback()
                results.append({"client_id": client_id, "ok": False, "conflict": True,
                                "log_id": log_id, "error": "서버에 더 최신 내용이 있습니다."})
                continue
            if item.get("submit"):
                cur.execute("UPDATE guard_logs SET status='submitted', submitted_at=NOW() WHERE id=%s",
                            (log_id,))
            conn.commit()
            cur.execute("SELECT updated_at FROM guard_logs WHERE id=%s", (log_id,))
            row = cur.fetchone()
            results.append({"client_id": client_id, "ok": True, "log_id": log_id,
                            "updated_at": _fmt_dt(row[0]) if row else None})
        except (ValueError, PermissionError) as e:
            conn.rollback()
            results.append({"client_id": client_id, "ok": False, "error": str(e)})
        except pymysql.MySQLError as e:
            conn.rollback()
            results.append({"client_id": client_id, "ok": False, "error": "DB 오류: %s" % e})
    conn.close()
    return jsonify(ok=True, results=results)


# ════════════════════════════════════════════════════════════
# API — 사진 첨부
# ════════════════════════════════════════════════════════════
@guard_bp.route("/api/guard/logs/<int:log_id>/photos", methods=["POST"])
@_api_auth
def api_guard_photo_upload(log_id):
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT guard_e_id FROM guard_logs WHERE id=%s", (log_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify(ok=False, error="일지를 찾을 수 없습니다."), 404
    if row[0] != g.guard_user.get("e_id") and not _is_admin_user():
        conn.close()
        return jsonify(ok=False, error="본인 일지에만 첨부할 수 있습니다."), 403

    f = request.files.get("photo")
    if not f or not f.filename:
        conn.close()
        return jsonify(ok=False, error="사진 파일이 없습니다."), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_PHOTO_EXT:
        conn.close()
        return jsonify(ok=False, error="지원하지 않는 형식입니다. (%s)" % ", ".join(sorted(ALLOWED_PHOTO_EXT))), 400

    os.makedirs(GUARD_UPLOAD_DIR, exist_ok=True)
    fname = "%d_%s%s" % (log_id, uuid.uuid4().hex[:12], ext)
    path = os.path.join(GUARD_UPLOAD_DIR, fname)
    f.save(path)
    size = os.path.getsize(path)
    if size > MAX_PHOTO_BYTES:
        os.remove(path)
        conn.close()
        return jsonify(ok=False, error="사진 용량은 10MB 이하여야 합니다."), 400

    cur.execute("""
        INSERT INTO guard_photos (log_id, filename, orig_name, filesize, uploaded_by)
        VALUES (%s,%s,%s,%s,%s)
    """, (log_id, fname, secure_filename(f.filename)[:200], size, g.guard_user.get("user_id")))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return jsonify(ok=True, photo={"id": pid, "filename": fname, "filesize": size,
                                   "url": "/guard/photo/%d" % pid})


@guard_bp.route("/api/guard/photos/<int:photo_id>", methods=["DELETE"])
@_api_auth
def api_guard_photo_delete(photo_id):
    conn = _conn(); cur = conn.cursor()
    cur.execute("""
        SELECT p.filename, l.guard_e_id FROM guard_photos p
          JOIN guard_logs l ON l.id = p.log_id WHERE p.id=%s
    """, (photo_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify(ok=False, error="사진을 찾을 수 없습니다."), 404
    if row[1] != g.guard_user.get("e_id") and not _is_admin_user():
        conn.close()
        return jsonify(ok=False, error="삭제 권한이 없습니다."), 403
    cur.execute("DELETE FROM guard_photos WHERE id=%s", (photo_id,))
    conn.commit(); conn.close()
    try:
        os.remove(os.path.join(GUARD_UPLOAD_DIR, row[0]))
    except OSError:
        pass
    return jsonify(ok=True)


@guard_bp.route("/guard/photo/<int:photo_id>")
@_api_auth
def guard_photo_serve(photo_id):
    conn = _conn(); cur = conn.cursor()
    cur.execute("""
        SELECT p.filename, l.guard_e_id FROM guard_photos p
          JOIN guard_logs l ON l.id = p.log_id WHERE p.id=%s
    """, (photo_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        abort(404)
    if row[1] != g.guard_user.get("e_id") and not _is_admin_user() \
            and "guard" not in (g.guard_user.get("permissions") or ""):
        abort(403)
    return send_from_directory(GUARD_UPLOAD_DIR, row[0])


# ════════════════════════════════════════════════════════════
# OTA — APK 버전 확인 / 다운로드 / 배포 관리
# ════════════════════════════════════════════════════════════
def _latest_release(cur):
    cur.execute("""
        SELECT version_code, version_name, filename, filesize, sha256, release_note,
               min_version_code, uploaded_at
          FROM guard_app_releases WHERE is_active=1
         ORDER BY version_code DESC LIMIT 1
    """)
    r = cur.fetchone()
    if not r:
        return None
    return {"version_code": r[0], "version_name": r[1], "filename": r[2], "filesize": r[3],
            "sha256": r[4] or "", "release_note": r[5] or "", "min_version_code": r[6] or 0,
            "uploaded_at": _fmt_dt(r[7]),
            "url": "/guard/app/download/%d" % r[0]}


@guard_bp.route("/api/guard/app/latest")
@_api_auth
def api_guard_app_latest():
    """앱이 시작 시 호출 — 최신 버전 유무와 강제 여부를 알려준다."""
    try:
        cur_vc = int(request.args.get("version_code", 0))
    except ValueError:
        cur_vc = 0
    conn = _conn(); cur = conn.cursor()
    rel = _latest_release(cur)
    conn.close()
    if not rel:
        return jsonify(ok=True, update_available=False, force=False, latest=None)
    return jsonify(ok=True,
                   update_available=rel["version_code"] > cur_vc,
                   force=cur_vc < (rel["min_version_code"] or 0),
                   latest=rel)


@guard_bp.route("/guard/app/download/<int:version_code>")
@_api_auth
def guard_app_download(version_code):
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT filename, version_name FROM guard_app_releases WHERE version_code=%s AND is_active=1",
                (version_code,))
    row = cur.fetchone()
    conn.close()
    if not row:
        abort(404)
    return send_from_directory(GUARD_APK_DIR, row[0], as_attachment=True,
                               download_name="taein-guard-%s.apk" % row[1],
                               mimetype="application/vnd.android.package-archive")


@guard_bp.route("/guard/app/install")
@_login_required
def guard_app_install():
    """최초 설치 안내 페이지 — 폰 브라우저로 접속해 APK 를 내려받는다."""
    conn = _conn(); cur = conn.cursor()
    rel = _latest_release(cur)
    conn.close()
    return render_template("guard_install.html", active_page="guard", rel=rel)


@guard_bp.route("/guard/admin/app", methods=["GET", "POST"])
@_admin_required
def guard_app_admin():
    conn = _conn(); cur = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "upload":
            f = request.files.get("apk")
            try:
                vc = int(request.form.get("version_code") or 0)
            except ValueError:
                vc = 0
            vn = (request.form.get("version_name") or "").strip()[:30]
            note = (request.form.get("release_note") or "").strip()
            try:
                min_vc = int(request.form.get("min_version_code") or 0)
            except ValueError:
                min_vc = 0

            err = None
            if not f or not f.filename:
                err = "APK 파일을 선택하세요."
            elif not f.filename.lower().endswith(".apk"):
                err = "APK 파일만 업로드할 수 있습니다."
            elif vc <= 0:
                err = "versionCode 는 1 이상의 정수여야 합니다."
            elif not vn:
                err = "versionName 을 입력하세요."
            else:
                cur.execute("SELECT COUNT(*) FROM guard_app_releases WHERE version_code=%s", (vc,))
                if cur.fetchone()[0]:
                    err = "versionCode %d 는 이미 등록되어 있습니다." % vc
            if err:
                flash(err, "danger")
                conn.close()
                return redirect("/guard/admin/app")

            os.makedirs(GUARD_APK_DIR, exist_ok=True)
            fname = "guard-%d-%s.apk" % (vc, uuid.uuid4().hex[:8])
            path = os.path.join(GUARD_APK_DIR, fname)
            f.save(path)
            size = os.path.getsize(path)
            if size > MAX_APK_BYTES:
                os.remove(path)
                flash("APK 용량은 %dMB 이하여야 합니다. (현재 %.1fMB)"
                      % (MAX_APK_BYTES // 1024 // 1024, size / 1024 / 1024), "danger")
                conn.close()
                return redirect("/guard/admin/app")

            h = hashlib.sha256()
            with open(path, "rb") as fp:
                for chunk in iter(lambda: fp.read(1024 * 1024), b""):
                    h.update(chunk)

            cur.execute("""
                INSERT INTO guard_app_releases (version_code, version_name, filename, filesize,
                                                sha256, release_note, min_version_code,
                                                is_active, uploaded_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s)
            """, (vc, vn, fname, size, h.hexdigest(), note, min_vc, session.get("user_id")))
            conn.commit()
            flash("v%s (versionCode %d) 를 배포했습니다. 앱이 다음 실행 시 업데이트를 안내합니다." % (vn, vc), "success")

        elif action == "toggle":
            rid = request.form.get("rid")
            cur.execute("UPDATE guard_app_releases SET is_active = 1 - is_active WHERE id=%s", (rid,))
            conn.commit()
            flash("배포 상태를 변경했습니다.", "success")

        elif action == "delete":
            rid = request.form.get("rid")
            cur.execute("SELECT filename FROM guard_app_releases WHERE id=%s", (rid,))
            row = cur.fetchone()
            cur.execute("DELETE FROM guard_app_releases WHERE id=%s", (rid,))
            conn.commit()
            if row:
                try:
                    os.remove(os.path.join(GUARD_APK_DIR, row[0]))
                except OSError:
                    pass
            flash("삭제했습니다.", "success")

        conn.close()
        return redirect("/guard/admin/app")

    cur.execute("""
        SELECT r.id, r.version_code, r.version_name, r.filesize, r.sha256, r.release_note,
               r.min_version_code, r.is_active, r.uploaded_at, u.name
          FROM guard_app_releases r
          LEFT JOIN app_users u ON u.id = r.uploaded_by
         ORDER BY r.version_code DESC
    """)
    releases = [{"id": r[0], "version_code": r[1], "version_name": r[2], "filesize": r[3],
                 "sha256": r[4] or "", "release_note": r[5] or "", "min_version_code": r[6] or 0,
                 "is_active": int(r[7]), "uploaded_at": r[8], "uploader": r[9] or "-"}
                for r in cur.fetchall()]
    conn.close()
    return render_template("guard_app.html", active_page="guard", releases=releases,
                           max_mb=MAX_APK_BYTES // 1024 // 1024)


# ════════════════════════════════════════════════════════════
# 웹 — 목록 / 인쇄 / 점검장소 관리
# ════════════════════════════════════════════════════════════
@guard_bp.route("/guard")
@_guard_required
def guard_list():
    ym = request.args.get("ym") or date.today().strftime("%Y-%m")
    try:
        y, m = [int(x) for x in ym.split("-")]
        first = date(y, m, 1)
    except ValueError:
        first = date.today().replace(day=1)
        y, m = first.year, first.month
    last = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)

    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM guard_points WHERE is_active=1")
    total_slots = (cur.fetchone()[0] or 0) * len(GUARD_ROUNDS)
    cur.execute("""
        SELECT l.id, l.log_date, l.weekday, l.guard_name, l.status, l.submitted_at,
               (SELECT COUNT(*) FROM guard_patrol p WHERE p.log_id=l.id AND p.result<>'미점검'),
               (SELECT COUNT(*) FROM guard_patrol p WHERE p.log_id=l.id AND p.result='이상'),
               (SELECT COUNT(*) FROM guard_visitors v WHERE v.log_id=l.id),
               (SELECT COUNT(*) FROM guard_photos g WHERE g.log_id=l.id)
          FROM guard_logs l
         WHERE l.log_date BETWEEN %s AND %s
         ORDER BY l.log_date DESC, l.id DESC
    """, (first, last))
    logs = [{"id": r[0], "log_date": r[1], "weekday": r[2], "guard_name": r[3],
             "status": r[4], "submitted_at": r[5], "patrol_done": r[6],
             "patrol_total": total_slots, "abnormal_cnt": r[7],
             "visitor_cnt": r[8], "photo_cnt": r[9]} for r in cur.fetchall()]
    conn.close()

    prev_ym = (first - timedelta(days=1)).strftime("%Y-%m")
    next_ym = (last + timedelta(days=1)).strftime("%Y-%m")
    return render_template("guard_list.html", active_page="guard", logs=logs,
                           ym=first.strftime("%Y-%m"), prev_ym=prev_ym, next_ym=next_ym,
                           is_admin=(session.get("role") == "admin"))


@guard_bp.route("/guard/<int:log_id>")
@_guard_required
def guard_print(log_id):
    conn = _conn(); cur = conn.cursor()
    log = _load_log(cur, log_id)
    if not log:
        conn.close()
        flash("일지를 찾을 수 없습니다.", "danger")
        return redirect("/guard")
    points = _load_points(cur)

    # 인쇄용 격자 — point_id → {round_key: {result, note}}
    grid = {}
    for p in log["patrol"]:
        grid.setdefault(p["point_id"], {})[p["round_key"]] = p
    rows = []
    for pt in points:
        cells = grid.get(pt["id"], {})
        notes = [cells.get(rk, {}).get("note", "") for rk in GUARD_ROUNDS]
        rows.append({
            "point": pt,
            "cells": [cells.get(rk, {"result": RESULT_NONE, "note": ""}) for rk in GUARD_ROUNDS],
            "note": " / ".join([n for n in notes if n]),
        })

    cur.execute("""
        SELECT id FROM guard_logs WHERE log_date < %s ORDER BY log_date DESC, id DESC LIMIT 1
    """, (log["log_date"],))
    r = cur.fetchone(); prev_id = r[0] if r else None
    cur.execute("""
        SELECT id FROM guard_logs WHERE log_date > %s ORDER BY log_date ASC, id ASC LIMIT 1
    """, (log["log_date"],))
    r = cur.fetchone(); next_id = r[0] if r else None
    conn.close()

    visitors = [v for v in log["visitors"] if v["block"] == "visitor"]
    staff = [v for v in log["visitors"] if v["block"] == "staff"]
    blank = max(6, len(visitors), len(staff))

    return render_template("guard_print.html", active_page="guard", log=log, rows=rows,
                           rounds=GUARD_ROUNDS, visitors=visitors, staff=staff,
                           blank_rows=blank, prev_id=prev_id, next_id=next_id,
                           result_ng=RESULT_NG, result_ok=RESULT_OK)


def _form_to_payload(form, points):
    """수정 화면의 폼 데이터를 _save_snapshot 이 받는 모양으로 바꾼다."""
    patrol = []
    for p in points:
        for rk in GUARD_ROUNDS:
            res = form.get("res_%d_%s" % (p["id"], rk)) or RESULT_NONE
            note = (form.get("note_%d_%s" % (p["id"], rk)) or "").strip()
            patrol.append({"point_id": p["id"], "round_key": rk, "result": res, "note": note,
                           "checked_at": form.get("at_%d_%s" % (p["id"], rk)) or None})

    visitors = []
    for blk in ("visitor", "staff"):
        idxs = form.getlist("%s_idx" % blk)
        for i in idxs:
            name = (form.get("%s_name_%s" % (blk, i)) or "").strip()
            company = (form.get("%s_company_%s" % (blk, i)) or "").strip()
            purpose = (form.get("%s_purpose_%s" % (blk, i)) or "").strip()
            car_no = (form.get("%s_car_%s" % (blk, i)) or "").strip()
            in_t = (form.get("%s_in_%s" % (blk, i)) or "").strip()
            out_t = (form.get("%s_out_%s" % (blk, i)) or "").strip()
            note = (form.get("%s_note_%s" % (blk, i)) or "").strip()
            if not any([name, company, purpose, car_no, in_t, out_t, note]):
                continue          # 빈 줄은 저장하지 않는다
            visitors.append({"block": blk, "in_time": in_t, "out_time": out_t,
                             "company": company, "name": name, "purpose": purpose,
                             "car_no": car_no, "note": note})

    return {
        "log_date": form.get("log_date"),
        "guard_e_id": form.get("guard_e_id"),
        "guard_name": form.get("guard_name"),
        "instructions": form.get("instructions") or "",
        "remarks": form.get("remarks") or "",
        "phone_memo": form.get("phone_memo") or "",
        "last_leaver": form.get("last_leaver") or "",
        "night_worker": form.get("night_worker") or "",
        "water_meter": form.get("water_meter") or "",
        "waste_request": form.get("waste_request") or "",
        "patrol": patrol,
        "visitors": visitors,
        "force": True,          # 웹에서 연 화면이 최신이므로 그대로 반영
    }


def _employee_options(cur):
    cur.execute("""
        SELECT id, name FROM tuser
         WHERE name IS NOT NULL AND name <> ''
         ORDER BY name
    """)
    return [{"id": r[0], "name": r[1]} for r in cur.fetchall()]


@guard_bp.route("/guard/new", methods=["GET", "POST"])
@guard_bp.route("/guard/<int:log_id>/edit", methods=["GET", "POST"])
@_guard_required
def guard_edit(log_id=None):
    conn = _conn(); cur = conn.cursor()
    points = _load_points(cur)

    if request.method == "POST":
        payload = _form_to_payload(request.form, points)
        actor = {"user_id": session.get("user_id"), "e_id": session.get("e_id"),
                 "name": session.get("user_name"), "role": session.get("role")}
        try:
            new_id, _ = _save_snapshot(cur, payload, actor, force=True, allow_any=True)
        except ValueError as e:
            conn.rollback(); conn.close()
            flash(str(e), "danger")
            return redirect("/guard/%d/edit" % log_id if log_id else "/guard/new")
        if request.form.get("status") == "submitted":
            cur.execute("UPDATE guard_logs SET status='submitted', submitted_at=NOW() WHERE id=%s",
                        (new_id,))
        else:
            cur.execute("UPDATE guard_logs SET status='draft', submitted_at=NULL WHERE id=%s",
                        (new_id,))
        conn.commit(); conn.close()
        flash("저장했습니다.", "success")
        return redirect("/guard/%d" % new_id)

    if log_id:
        log = _load_log(cur, log_id)
        if not log:
            conn.close()
            flash("일지를 찾을 수 없습니다.", "danger")
            return redirect("/guard")
    else:
        log = {"id": None, "log_date": date.today().strftime("%Y-%m-%d"),
               "weekday": _weekday_kr(date.today()),
               "guard_e_id": session.get("e_id"), "guard_name": session.get("user_name") or "",
               "instructions": "", "remarks": "", "phone_memo": "", "last_leaver": "",
               "night_worker": "", "water_meter": "", "waste_request": "",
               "status": "draft", "patrol": [], "visitors": [], "photos": []}
        ins = _instruction_for(cur, date.today())
        if ins:
            log["instructions"] = ins["content"]

    grid = {}
    for p in log["patrol"]:
        grid.setdefault(p["point_id"], {})[p["round_key"]] = p
    rows = [{"point": pt,
             "cells": [grid.get(pt["id"], {}).get(rk, {"result": RESULT_NONE, "note": "",
                                                       "checked_at": None})
                       for rk in GUARD_ROUNDS]} for pt in points]

    visitors = [v for v in log["visitors"] if v["block"] == "visitor"]
    staff = [v for v in log["visitors"] if v["block"] == "staff"]
    employees = _employee_options(cur)
    conn.close()

    return render_template("guard_edit.html", active_page="guard", log=log, rows=rows,
                           rounds=GUARD_ROUNDS, visitors=visitors, staff=staff,
                           employees=employees, results=VALID_RESULTS,
                           is_new=(log_id is None),
                           is_admin=(session.get("role") == "admin"))


@guard_bp.route("/guard/<int:log_id>/delete", methods=["POST"])
@_admin_required
def guard_delete(log_id):
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT log_date, guard_name FROM guard_logs WHERE id=%s", (log_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        flash("일지를 찾을 수 없습니다.", "danger")
        return redirect("/guard")
    files = _delete_log(cur, log_id)
    conn.commit(); conn.close()
    for fn in files:
        try:
            os.remove(os.path.join(GUARD_UPLOAD_DIR, fn))
        except OSError:
            pass
    flash("%s %s 일지를 삭제했습니다." % (row[0].strftime("%Y-%m-%d"), row[1]), "success")
    return redirect("/guard")


@guard_bp.route("/guard/instructions", methods=["GET", "POST"])
@_guard_required
def guard_instructions():
    """지시사항 등록 — 날짜별로 미리 걸어두면 그날 일지에 자동으로 들어간다."""
    can_edit = session.get("role") == "admin" or _has_perm("guard")
    conn = _conn(); cur = conn.cursor()

    if request.method == "POST":
        if not can_edit:
            conn.close()
            flash("등록 권한이 없습니다.", "danger")
            return redirect("/guard/instructions")

        action = request.form.get("action")
        if action == "delete":
            cur.execute("DELETE FROM guard_instructions WHERE id=%s", (request.form.get("iid"),))
            flash("삭제했습니다.", "success")
        else:
            d = _parse_date(request.form.get("target_date"))
            content = (request.form.get("content") or "").strip()
            if not d:
                flash("날짜를 선택하세요.", "danger")
            elif not content:
                flash("지시사항 내용을 입력하세요.", "danger")
            else:
                cur.execute("""
                    INSERT INTO guard_instructions (target_date, content, created_by, created_by_name)
                    VALUES (%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE content=VALUES(content),
                                            created_by=VALUES(created_by),
                                            created_by_name=VALUES(created_by_name)
                """, (d, content, session.get("user_id"), session.get("user_name") or ""))
                # 이미 작성 시작된 그날 일지 중 지시사항이 비어 있는 건에 반영
                cur.execute("""
                    UPDATE guard_logs SET instructions=%s
                     WHERE log_date=%s AND (instructions IS NULL OR instructions='')
                """, (content, d))
                flash("%s 지시사항을 등록했습니다." % d.strftime("%Y-%m-%d"), "success")
        conn.commit(); conn.close()
        return redirect("/guard/instructions")

    cur.execute("""
        SELECT i.id, i.target_date, i.content, i.created_by_name, i.updated_at,
               (SELECT COUNT(*) FROM guard_logs l WHERE l.log_date = i.target_date)
          FROM guard_instructions i
         ORDER BY i.target_date DESC
         LIMIT 120
    """)
    rows = [{"id": r[0], "target_date": r[1], "content": r[2] or "", "author": r[3] or "",
             "updated_at": r[4], "log_cnt": r[5]} for r in cur.fetchall()]
    conn.close()
    return render_template("guard_instructions.html", active_page="guard",
                           items=rows, today=date.today().strftime("%Y-%m-%d"),
                           can_edit=can_edit)


@guard_bp.route("/guard/admin/points", methods=["GET", "POST"])
@_admin_required
def guard_points_admin():
    conn = _conn(); cur = conn.cursor()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            nm = (request.form.get("name") or "").strip()
            if nm:
                cur.execute("SELECT COALESCE(MAX(seq),0)+1 FROM guard_points")
                next_seq = cur.fetchone()[0]
                cur.execute("INSERT INTO guard_points (seq, name, is_active) VALUES (%s,%s,1)",
                            (next_seq, nm))
                flash("점검장소를 추가했습니다.", "success")
        elif action == "save":
            for pid in request.form.getlist("pid"):
                nm = (request.form.get("name_%s" % pid) or "").strip()
                seq = request.form.get("seq_%s" % pid) or 0
                act = 1 if request.form.get("active_%s" % pid) else 0
                if nm:
                    cur.execute("UPDATE guard_points SET name=%s, seq=%s, is_active=%s WHERE id=%s",
                                (nm, int(seq), act, int(pid)))
            flash("저장했습니다.", "success")
        conn.commit()
        conn.close()
        return redirect("/guard/admin/points")

    points = _load_points(cur, active_only=False)
    conn.close()
    return render_template("guard_points.html", active_page="guard", points=points)
