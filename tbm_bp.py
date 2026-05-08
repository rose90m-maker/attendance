"""㈜ 태인 Tool Box Meeting 관리 — Flask Blueprint
메인 app_maria.py로 통합된 버전. url_prefix=/tbm, template_folder=tbm
세션 키는 메인 앱과 통일(user_id/role/e_id/user_name) — SSO 효과.
순환 import 회피 위해 DB 헬퍼·데코레이터는 self-contained.
"""
import os
import json
import hashlib
from datetime import datetime, date, timedelta
from functools import wraps

import pymysql
from flask import (Blueprint, render_template, request, flash, jsonify,
                   redirect, url_for, session)

tbm_bp = Blueprint("tbm", __name__, url_prefix="/tbm")


# ── DB 설정 (메인 앱과 동일 DB 공유) ────────────────────────
MARIA = {
    "host":     os.environ.get("DB_HOST", "127.0.0.1"),
    "port":     int(os.environ.get("DB_PORT", "3307")),
    "user":     os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", "7602mr"),
    "db":       os.environ.get("DB_NAME", "attendance"),
    "charset":  "utf8mb4",
}


def _conn():
    return pymysql.connect(**MARIA)


def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


# ── 데코레이터 (메인 앱 세션 키 기반) ──────────────────────
def _login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("tbm.tbm_login"))
        return f(*args, **kwargs)
    return decorated


def _admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("tbm.tbm_login"))
        if session.get("role") != "admin":
            flash("관리자 권한이 필요합니다.", "danger")
            return redirect(url_for("tbm.tbm_index"))
        return f(*args, **kwargs)
    return decorated


# ── 사업부 매핑 (employee_roster.dept 기준) ──────────────
TBM_DEPTS = ["전자사업부", "전기사업부"]

COMPANY_TO_TBM_DEPT = {
    "0002000000000000": "전기사업부",
    "0003000000000000": "전자사업부",
    "0004000000000000": "전기사업부",
    "0005000000000000": "전자사업부",
}


def _classify_dept(dept: str):
    d = (dept or "").strip()
    if d.startswith("전기") or d == "자재":
        return "전기사업부"
    if d.startswith("전자") or d in ("PQC", "QA", "SAW", "SMT", "설비", "수리"):
        return "전자사업부"
    return None


SAFETY_ITEMS = [
    "1) 설비 작동 기능 점검",
    "2) 안전 장치 유지 확인",
    "3) 안전 보호구 착용 확인",
]
TBM_DEFAULTS = {
    "전기사업부": {
        "job_name":     "누전 차단기 제조",
        "job_content":  "제조 조립원",
        "location":     "1층 현장",
        "leader_dept":  "전기사업부",
        "leader_name":  "",
        "leader_position": "",
        "safety_instruction": "작업자 이상 상태 無, 설비 이상 無, 안전 장치 이상 無/ 재활용 참여 독려 /안전보건 규정 변경(26.01)",
    },
    "전자사업부": {
        "job_name":     "메모리 모듈 생산",
        "job_content":  "제조 조립원",
        "location":     "2층 현장",
        "leader_dept":  "전자사업부",
        "leader_name":  "",
        "leader_position": "",
        "safety_instruction": "작업자 이상 상태 無, 설비 이상 無, 안전 장치 이상 無/ 재활용 참여 독려 /안전보건 규정 변경(26.01)",
    },
}
ABSENCE_TYPES = ["연차", "반차", "무급휴가", "결근", "병가"]

TBM_EXCLUDE_ETC = {
    "연차":  "연차",
    "무급":  "무급휴가",
    "경조":  "경조",
    "휴가":  "휴가",
}

DEPT_SHEETS = {
    "전기사업부": ["전기", "전기생산"],
    "전자사업부": ["전자", "전자자재", "saw", "SAW"],
}

DEPT_COMPANY_CODES = {
    "전기사업부": ("0002000000000000", "0004000000000000"),
    "전자사업부": ("0003000000000000", "0005000000000000"),
}


# ── DB 초기화 (메인 _init_db()에서 호출) ──────────────────
def init_tbm_db(cur):
    """TBM 6개 테이블 생성 — 메인 앱의 _init_db()에서 호출 (IF NOT EXISTS)"""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS `tbm_weeks` (
            `id`         INT AUTO_INCREMENT PRIMARY KEY,
            `year`       INT NOT NULL,
            `week_no`    INT NOT NULL,
            `week_label` VARCHAR(30) NOT NULL DEFAULT '',
            `tbm_date`   DATE NOT NULL,
            `status`     VARCHAR(10) NOT NULL DEFAULT 'open',
            `created_by` VARCHAR(30) NOT NULL DEFAULT '',
            `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY `uq_tbm_week` (`year`,`week_no`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS `tbm_sessions` (
            `id`                  INT AUTO_INCREMENT PRIMARY KEY,
            `week_id`             INT NOT NULL,
            `dept`                VARCHAR(20) NOT NULL,
            `time_start`          VARCHAR(10) NOT NULL DEFAULT '08:30',
            `time_end`            VARCHAR(10) NOT NULL DEFAULT '08:40',
            `same_as_job_date`    TINYINT(1) NOT NULL DEFAULT 1,
            `job_name`            VARCHAR(200) NOT NULL DEFAULT '',
            `job_content`         VARCHAR(200) NOT NULL DEFAULT '',
            `location`            VARCHAR(100) NOT NULL DEFAULT '',
            `risk_assessed`       VARCHAR(2) NOT NULL DEFAULT 'Y',
            `key_hazard_select`   TEXT,
            `key_hazard_measure`  TEXT,
            `leader_dept`         VARCHAR(50) NOT NULL DEFAULT '',
            `leader_position`     VARCHAR(30) NOT NULL DEFAULT '',
            `leader_name`         VARCHAR(20) NOT NULL DEFAULT '',
            `safety_check1`       VARCHAR(2) NOT NULL DEFAULT 'Y',
            `safety_check2`       VARCHAR(2) NOT NULL DEFAULT 'Y',
            `safety_check3`       VARCHAR(2) NOT NULL DEFAULT 'Y',
            `safety_instruction`  TEXT,
            `created_at`          DATETIME DEFAULT CURRENT_TIMESTAMP,
            `updated_at`          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY `uq_tbm_session` (`week_id`,`dept`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS `tbm_hazards` (
            `id`          INT AUTO_INCREMENT PRIMARY KEY,
            `session_id`  INT NOT NULL,
            `sort_order`  INT NOT NULL DEFAULT 0,
            `hazard`      TEXT NOT NULL,
            `measure`     TEXT NOT NULL,
            INDEX `idx_tbm_haz_sess` (`session_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS `tbm_attendance` (
            `id`             INT AUTO_INCREMENT PRIMARY KEY,
            `session_id`     INT NOT NULL,
            `e_id`           INT NOT NULL,
            `e_name`         VARCHAR(30) NOT NULL DEFAULT '',
            `e_dept`         VARCHAR(30) NOT NULL DEFAULT '',
            `signature_data` MEDIUMTEXT,
            `signed_at`      DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY `uq_tbm_attend` (`session_id`,`e_id`),
            INDEX `idx_tbm_att_sess` (`session_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS `tbm_exclusions` (
            `id`         INT AUTO_INCREMENT PRIMARY KEY,
            `week_id`    INT NOT NULL,
            `e_id`       INT NOT NULL,
            `e_name`     VARCHAR(30) NOT NULL DEFAULT '',
            `e_dept`     VARCHAR(30) NOT NULL DEFAULT '',
            `reason`     VARCHAR(20) NOT NULL DEFAULT '연차',
            `created_by` VARCHAR(30) NOT NULL DEFAULT '',
            `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY `uq_tbm_excl` (`week_id`,`e_id`),
            INDEX `idx_tbm_excl_week` (`week_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS `tbm_templates` (
            `id`           INT AUTO_INCREMENT PRIMARY KEY,
            `dept`         VARCHAR(20) NOT NULL,
            `template_key` VARCHAR(30) NOT NULL,
            `content`      TEXT NOT NULL DEFAULT '',
            `updated_at`   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            `updated_by`   VARCHAR(30) NOT NULL DEFAULT '',
            UNIQUE KEY `uq_tmpl` (`dept`, `template_key`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    try:
        cur.execute(
            "ALTER TABLE tbm_attendance "
            "ADD COLUMN emp_no VARCHAR(20) NOT NULL DEFAULT '' AFTER e_id"
        )
    except Exception:
        pass


# ── 헬퍼 ────────────────────────────────────────
def _week_label(year, week_no):
    m, w = week_no // 10, week_no % 10
    if 1 <= m <= 12 and 1 <= w <= 5:
        return f"{year}년 {m}월 {w}주차"
    return f"{year}년 {week_no}주차"


def _this_week():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    wednesday = monday + timedelta(days=2)
    month = wednesday.month
    week_in_month = (wednesday.day - 1) // 7 + 1
    return wednesday.year, month * 10 + week_in_month, wednesday


def _get_employees_by_dept(dept, work_date=None):
    if dept not in TBM_DEPTS:
        return []
    if dept == "전기사업부":
        er_dept_cond = "(er.dept LIKE '전기%%' OR er.dept = '자재')"
    else:
        er_dept_cond = "(er.dept LIKE '전자%%' OR er.dept IN ('PQC','QA','SAW','SMT','설비','수리'))"
    conn = _conn()
    try:
        cur = conn.cursor()
        if work_date is not None:
            sheet_names = DEPT_SHEETS.get(dept, [])
            if not sheet_names:
                return []
            if isinstance(work_date, (date, datetime)):
                work_date_str = work_date.strftime("%Y%m%d")
            else:
                work_date_str = str(work_date)
            ph_s = ",".join(["%s"] * len(sheet_names))
            cur.execute(f"""
                SELECT sr.emp_name, sr.sheet_name,
                       COALESCE(er.id, er_any.id, 0) AS er_id,
                       COALESCE(er.emp_no, er_any.emp_no, '') AS emp_no
                FROM schedule_record sr
                LEFT JOIN employee_roster er
                    ON er.name = sr.emp_name AND {er_dept_cond}
                LEFT JOIN (
                    SELECT r1.id, r1.name, r1.emp_no
                    FROM employee_roster r1
                    JOIN (SELECT name, MIN(id) AS min_id FROM employee_roster GROUP BY name) r2
                        ON r1.id = r2.min_id
                ) er_any ON er_any.name = sr.emp_name
                WHERE sr.work_date = %s AND sr.sheet_name IN ({ph_s})
                ORDER BY sr.sheet_name, sr.emp_name
            """, [work_date_str] + list(sheet_names))
            seen = set()
            result = []
            fallback_idx = 0
            for emp_name, sheet_name, er_id, emp_no in cur.fetchall():
                dedup_key = emp_no if emp_no else emp_name
                if dedup_key in seen: continue
                seen.add(dedup_key)
                if er_id == 0:
                    fallback_idx -= 1
                    er_id = fallback_idx
                result.append({"e_id": er_id, "e_name": emp_name,
                               "e_sub_dept": sheet_name, "emp_no": emp_no})
            return result
        else:
            cur.execute(f"""
                SELECT er.id, er.name, er.dept, COALESCE(er.emp_no, '')
                FROM employee_roster er
                WHERE {er_dept_cond}
                ORDER BY er.dept, er.name
            """)
            return [{"e_id": r[0], "e_name": r[1], "e_sub_dept": r[2], "emp_no": r[3]}
                    for r in cur.fetchall()]
    finally:
        conn.close()


def _get_user_dept(e_id_tuser):
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT er.dept FROM tuser t
            JOIN employee_roster er ON er.emp_no = t.idno
            WHERE t.id = %s LIMIT 1
        """, (e_id_tuser,))
        row = cur.fetchone()
        if row:
            raw_dept = (row[0] or "").strip()
            if raw_dept == "자재":
                cur.execute("SELECT company FROM tuser WHERE id = %s", (e_id_tuser,))
                c_row = cur.fetchone()
                if c_row:
                    mapped = COMPANY_TO_TBM_DEPT.get((c_row[0] or "").strip())
                    if mapped: return mapped
            d = _classify_dept(raw_dept)
            if d: return d
        cur.execute("SELECT company FROM tuser WHERE id = %s", (e_id_tuser,))
        row = cur.fetchone()
        if row:
            return COMPANY_TO_TBM_DEPT.get((row[0] or "").strip())
        return None
    finally:
        conn.close()


def _get_schedule_statuses(emp_names, work_date_str):
    if not emp_names or not work_date_str:
        return {}
    conn = _conn()
    try:
        cur = conn.cursor()
        ph = ",".join(["%s"] * len(emp_names))
        cur.execute(
            f"SELECT emp_name, etc FROM schedule_record "
            f"WHERE work_date=%s AND emp_name IN ({ph})",
            [work_date_str] + list(emp_names),
        )
        result = {}
        priority = list(TBM_EXCLUDE_ETC.keys())
        for emp_name, etc in cur.fetchall():
            val = (etc or "").strip()
            if val in priority:
                result[emp_name] = val
            elif emp_name not in result:
                result[emp_name] = val
        return result
    finally:
        conn.close()


def _auto_excl_from_schedule(emps, tbm_date):
    if not tbm_date:
        return {}
    date_str = tbm_date.strftime("%Y%m%d") if hasattr(tbm_date, "strftime") else str(tbm_date).replace("-", "")
    statuses = _get_schedule_statuses([e["e_name"] for e in emps], date_str)
    result = {}
    for e in emps:
        etc = statuses.get(e["e_name"], "")
        reason = TBM_EXCLUDE_ETC.get(etc)
        if reason:
            result[e["e_id"]] = {"e_name": e["e_name"], "reason": f"[근무표] {reason}",
                                 "emp_no": e.get("emp_no", "")}
    return result


def _get_er_id(tuser_id):
    if not tuser_id:
        return None
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT er.id, er.emp_no FROM tuser t
            JOIN employee_roster er ON er.emp_no = t.idno
            WHERE t.id = %s
        """, (tuser_id,))
        return cur.fetchone()
    finally:
        conn.close()


def _get_templates(dept=None):
    conn = _conn()
    try:
        cur = conn.cursor()
        if dept:
            cur.execute(
                "SELECT dept, template_key, content FROM tbm_templates "
                "WHERE dept IN (%s, '공통') ORDER BY dept", (dept,)
            )
        else:
            cur.execute("SELECT dept, template_key, content FROM tbm_templates ORDER BY dept, template_key")
        result = {}
        for d, k, c in cur.fetchall():
            result.setdefault(d, {})[k] = c
        return result
    finally:
        conn.close()


def _get_manager_dept_options():
    result = {d: [] for d in (TBM_DEPTS + ["공통"])}
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT name, dept, position FROM employee_roster
            WHERE IFNULL(position, '') <> ''
              AND position NOT IN ('사원', '생산직')
            ORDER BY dept, name
        """)
        for name, raw_dept, pos in cur.fetchall():
            tbm_dept = _classify_dept(raw_dept)
            if not tbm_dept: continue
            item = {"tbm_dept": tbm_dept, "dept": (raw_dept or "").strip(),
                    "name": (name or "").strip(), "position": (pos or "").strip()}
            result[tbm_dept].append(item)
            result["공통"].append(item)
        return result
    finally:
        conn.close()


def _week_stats(cur, week_id):
    cur.execute("SELECT tbm_date FROM tbm_weeks WHERE id=%s", (week_id,))
    row = cur.fetchone()
    ref_date = row[0] if row else None
    stats = {}
    for dept in TBM_DEPTS:
        emps = _get_employees_by_dept(dept, ref_date)
        auto_excl = _auto_excl_from_schedule(emps, ref_date)
        cur.execute("SELECT id FROM tbm_sessions WHERE week_id=%s AND dept=%s", (week_id, dept))
        sess = cur.fetchone()
        signed = 0
        if sess:
            cur.execute("SELECT COUNT(*) FROM tbm_attendance WHERE session_id=%s", (sess[0],))
            signed = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM tbm_exclusions WHERE week_id=%s AND e_dept=%s", (week_id, dept))
        manual_excl_cnt = cur.fetchone()[0]
        total_excl = len(auto_excl) + manual_excl_cnt
        stats[dept] = {
            "total": len(emps), "excluded": total_excl,
            "target": len(emps) - total_excl, "signed": signed,
            "missing": max(0, len(emps) - total_excl - signed),
            "session_id": sess[0] if sess else None,
        }
    return stats


# ════════════════════════════════════════════════════════
# 로그인 / 로그아웃
# ════════════════════════════════════════════════════════
@tbm_bp.route("/login", methods=["GET", "POST"])
def tbm_login():
    if session.get("user_id"):
        return redirect(url_for("tbm.tbm_index"))
    if request.method == "POST":
        login_id = request.form.get("login_id", "").strip()
        password = request.form.get("password", "").strip()
        if not login_id or not password:
            flash("아이디와 비밀번호를 입력하세요.", "danger")
            return render_template("tbm/login.html")
        conn = _conn(); cur = conn.cursor()
        cur.execute(
            "SELECT id, login_id, password, name, e_id, role "
            "FROM app_users WHERE login_id=%s", (login_id,)
        )
        row = cur.fetchone()
        conn.close()
        if not row or row[2] != _hash_pw(password):
            flash("아이디 또는 비밀번호가 틀립니다.", "danger")
            return render_template("tbm/login.html")
        # 메인 앱 세션 키 사용 (SSO 효과)
        session["user_id"]   = row[0]
        session["login_id"]  = row[1]
        session["user_name"] = row[3]
        session["e_id"]      = row[4]
        session["role"]      = row[5]
        return redirect(url_for("tbm.tbm_index"))
    return render_template("tbm/login.html")


@tbm_bp.route("/logout")
def tbm_logout():
    # 메인 세션까지 로그아웃 — 두 앱 통합으로 별도 TBM 로그아웃 의미 없어짐
    session.clear()
    return redirect(url_for("tbm.tbm_login"))


# ════════════════════════════════════════════════════════
# 메인 진입점
# ════════════════════════════════════════════════════════
@tbm_bp.route("/")
@tbm_bp.route("")
@_login_required
def tbm_index():
    if session.get("role") == "admin":
        return redirect(url_for("tbm.tbm_dashboard"))
    return redirect(url_for("tbm.tbm_sign"))


# ════════════════════════════════════════════════════════
# 관리자 대시보드
# ════════════════════════════════════════════════════════
@tbm_bp.route("/admin")
@_admin_required
def tbm_dashboard():
    year, week_no, wednesday = _this_week()
    conn = _conn(); cur = conn.cursor()
    cur.execute(
        "SELECT id, year, week_no, week_label, tbm_date, status, created_at "
        "FROM tbm_weeks WHERE status IN ('open','closed') ORDER BY year DESC, week_no DESC LIMIT 30"
    )
    rows = cur.fetchall()
    weeks = []
    for r in rows:
        wid, wy, wn, wl, wd, wst, wca = r
        stats = _week_stats(cur, wid)
        weeks.append({
            "id": wid, "year": wy, "week_no": wn,
            "label": wl or _week_label(wy, wn),
            "tbm_date": wd, "status": wst, "created_at": wca,
            "stats": stats,
        })
    cur.execute("SELECT id FROM tbm_weeks WHERE year=%s AND week_no=%s AND status IN ('open','closed')", (year, week_no))
    has_this = bool(cur.fetchone())
    conn.close()
    this_month = week_no // 10
    this_week_in_month = week_no % 10
    return render_template(
        "tbm/dashboard.html",
        weeks=weeks, this_year=year, this_week_no=week_no,
        this_month=this_month, this_week_in_month=this_week_in_month,
        this_wednesday=wednesday, has_this_week=has_this,
        tbm_depts=TBM_DEPTS,
        user_name=session.get("user_name"),
    )


@tbm_bp.route("/admin/week/create", methods=["POST"])
@_admin_required
def tbm_week_create():
    try:
        year          = int(request.form.get("year", 0))
        month         = int(request.form.get("month", 0))
        week_in_month = int(request.form.get("week_in_month", 0))
        tbm_date      = request.form.get("tbm_date", "")
    except (ValueError, TypeError):
        flash("입력값이 올바르지 않습니다.", "danger")
        return redirect(url_for("tbm.tbm_dashboard"))
    if not year or not (1 <= month <= 12) or not (1 <= week_in_month <= 5) or not tbm_date:
        flash("주차 정보를 올바르게 입력하세요.", "danger")
        return redirect(url_for("tbm.tbm_dashboard"))
    week_no = month * 10 + week_in_month
    label = _week_label(year, week_no)
    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM tbm_weeks WHERE year=%s AND week_no=%s AND status='draft'",
            (year, week_no),
        )
        existing_draft = cur.fetchone()
        if existing_draft:
            flash(f"{label} 주차 작성 페이지입니다. 내용을 입력 후 저장하세요.", "info")
            return redirect(url_for("tbm.tbm_week_detail", week_id=existing_draft[0]))
        cur.execute(
            "INSERT INTO tbm_weeks (year, week_no, week_label, tbm_date, status, created_by) "
            "VALUES (%s,%s,%s,%s,'draft',%s)",
            (year, week_no, label, tbm_date, session.get("user_name", "")),
        )
        week_id = cur.lastrowid
        conn.commit()
        flash(f"{label} 주차 작성 페이지입니다. 내용을 입력 후 저장하세요.", "info")
        return redirect(url_for("tbm.tbm_week_detail", week_id=week_id))
    except pymysql.IntegrityError:
        flash("이미 존재하는 주차입니다.", "warning")
        return redirect(url_for("tbm.tbm_dashboard"))
    finally:
        conn.close()


@tbm_bp.route("/admin/week/<int:week_id>")
@_admin_required
def tbm_week_detail(week_id):
    conn = _conn(); cur = conn.cursor()
    cur.execute(
        "SELECT id, year, week_no, week_label, tbm_date, status FROM tbm_weeks WHERE id=%s",
        (week_id,)
    )
    week_row = cur.fetchone()
    if not week_row:
        flash("존재하지 않는 주차입니다.", "danger")
        conn.close()
        return redirect(url_for("tbm.tbm_dashboard"))
    week = {"id": week_row[0], "year": week_row[1], "week_no": week_row[2],
            "label": week_row[3] or _week_label(week_row[1], week_row[2]),
            "tbm_date": week_row[4], "status": week_row[5]}
    sessions, hazards, attendance, exclusions = {}, {}, {}, {}
    for dept in TBM_DEPTS:
        cur.execute("SELECT * FROM tbm_sessions WHERE week_id=%s AND dept=%s", (week_id, dept))
        row = cur.fetchone()
        if row:
            cols = [d[0] for d in cur.description]
            sess = dict(zip(cols, row))
            sessions[dept] = sess
            cur.execute(
                "SELECT id, sort_order, hazard, measure FROM tbm_hazards "
                "WHERE session_id=%s ORDER BY sort_order", (sess["id"],)
            )
            hazards[dept] = [{"id": r[0], "hazard": r[2], "measure": r[3]} for r in cur.fetchall()]
        else:
            sessions[dept] = None
            hazards[dept] = []
        emps = _get_employees_by_dept(dept, week["tbm_date"])
        auto_excl = _auto_excl_from_schedule(emps, week["tbm_date"])
        signed_ids = set()
        if sessions[dept]:
            cur.execute("SELECT e_id FROM tbm_attendance WHERE session_id=%s", (sessions[dept]["id"],))
            signed_ids = {r[0] for r in cur.fetchall()}
        cur.execute(
            "SELECT e_id, e_name, reason FROM tbm_exclusions WHERE week_id=%s AND e_dept=%s",
            (week_id, dept)
        )
        manual_excl = {r[0]: {"e_name": r[1], "reason": r[2]} for r in cur.fetchall()}
        all_excl_ids = set(auto_excl.keys()) | set(manual_excl.keys())
        exclusions[dept] = manual_excl
        attendance[dept] = {
            "total": len(emps), "signed": len(signed_ids),
            "excluded": len(all_excl_ids), "target": len(emps) - len(all_excl_ids),
            "missing": [e for e in emps if e["e_id"] not in signed_ids and e["e_id"] not in all_excl_ids],
            "signed_list": [e for e in emps if e["e_id"] in signed_ids],
            "auto_excl_list": sorted(auto_excl.values(), key=lambda x: x["e_name"]),
        }
    roster_positions = {}
    roster_members = {}
    for dept in TBM_DEPTS:
        prefix = "전기" if "전기" in dept else "전자"
        cur.execute(
            "SELECT name, position, dept FROM employee_roster WHERE dept LIKE %s ORDER BY dept, name",
            (prefix + '%',)
        )
        rows = cur.fetchall()
        roster_positions[dept] = {r[0]: r[1] for r in rows}
        roster_members[dept] = [
            {"name": (r[0] or "").strip(),
             "position": (r[1] or "").strip(),
             "org_dept": (r[2] or "").strip()}
            for r in rows if (r[0] or "").strip()
        ]
    tbm_defaults = {}
    for dept in TBM_DEPTS:
        tmpl_map = _get_templates(dept)
        combined_tmpl = {**tmpl_map.get("공통", {}), **tmpl_map.get(dept, {})}
        base = dict(TBM_DEFAULTS.get(dept, {}))
        for k in ("job_name","job_content","location","leader_dept","leader_name","leader_position"):
            if combined_tmpl.get(k):
                base[k] = combined_tmpl[k]
        tbm_defaults[dept] = base
    conn.close()
    return render_template(
        "tbm/week_detail.html",
        week=week, sessions=sessions, hazards=hazards,
        attendance=attendance, exclusions=exclusions,
        tbm_depts=TBM_DEPTS, safety_items=SAFETY_ITEMS,
        absence_types=ABSENCE_TYPES,
        tbm_defaults=tbm_defaults,
        roster_positions=roster_positions,
        roster_members=roster_members,
        user_name=session.get("user_name"),
    )


@tbm_bp.route("/admin/week/<int:week_id>/session", methods=["POST"])
@_admin_required
def tbm_session_save(week_id):
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT status FROM tbm_weeks WHERE id=%s", (week_id,))
    wk = cur.fetchone()
    if not wk:
        conn.close(); return jsonify({"ok": False, "error": "주차 없음"}), 404
    if wk[0] == "closed":
        conn.close(); return jsonify({"ok": False, "error": "마감된 주차입니다"}), 403
    is_draft = (wk[0] == "draft")
    dept = request.form.get("dept", "")
    if dept not in TBM_DEPTS:
        conn.close(); return jsonify({"ok": False, "error": "잘못된 사업부"}), 400
    fields = {
        "time_start":         request.form.get("time_start", "08:30"),
        "time_end":           request.form.get("time_end", "08:40"),
        "same_as_job_date":   1 if request.form.get("same_as_job_date") == "Y" else 0,
        "job_name":           (request.form.get("job_name", ""))[:200],
        "job_content":        (request.form.get("job_content", ""))[:200],
        "location":           (request.form.get("location", ""))[:100],
        "risk_assessed":      request.form.get("risk_assessed", "Y"),
        "key_hazard_select":  request.form.get("key_hazard_select", ""),
        "key_hazard_measure": request.form.get("key_hazard_measure", ""),
        "leader_dept":        (request.form.get("leader_dept", ""))[:50],
        "leader_position":    (request.form.get("leader_position", ""))[:30],
        "leader_name":        (request.form.get("leader_name", ""))[:20],
        "safety_check1":      request.form.get("safety_check1", "Y"),
        "safety_check2":      request.form.get("safety_check2", "Y"),
        "safety_check3":      request.form.get("safety_check3", "Y"),
        "safety_instruction": request.form.get("safety_instruction", ""),
    }
    try:
        cur.execute("SELECT id FROM tbm_sessions WHERE week_id=%s AND dept=%s", (week_id, dept))
        existing = cur.fetchone()
        if existing:
            set_clause = ", ".join([f"`{k}`=%s" for k in fields])
            cur.execute(
                f"UPDATE tbm_sessions SET {set_clause} WHERE id=%s",
                list(fields.values()) + [existing[0]]
            )
            session_id = existing[0]
        else:
            cols = ", ".join([f"`{k}`" for k in fields])
            ph   = ", ".join(["%s"] * len(fields))
            cur.execute(
                f"INSERT INTO tbm_sessions (week_id, dept, {cols}) VALUES (%s,%s,{ph})",
                [week_id, dept] + list(fields.values())
            )
            session_id = cur.lastrowid
        raw_hazards = request.form.get("hazards_json", "[]")
        try:
            hazards = json.loads(raw_hazards)
        except json.JSONDecodeError:
            hazards = []
        cur.execute("DELETE FROM tbm_hazards WHERE session_id=%s", (session_id,))
        for i, h in enumerate(hazards):
            hazard  = str(h.get("hazard", "")).strip()
            measure = str(h.get("measure", "")).strip()
            if hazard or measure:
                cur.execute(
                    "INSERT INTO tbm_hazards (session_id, sort_order, hazard, measure) "
                    "VALUES (%s,%s,%s,%s)",
                    (session_id, i, hazard, measure)
                )
        conn.commit()
        if is_draft:
            cur2 = conn.cursor()
            cur2.execute("UPDATE tbm_weeks SET status='open' WHERE id=%s AND status='draft'", (week_id,))
            conn.commit()
        return jsonify({"ok": True, "session_id": session_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


@tbm_bp.route("/admin/week/<int:week_id>/exclude", methods=["POST"])
@_admin_required
def tbm_exclude(week_id):
    data   = request.get_json(silent=True) or {}
    action = data.get("action", "add")
    try:
        e_id = int(data.get("e_id", 0))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "e_id invalid"}), 400
    if not e_id:
        return jsonify({"ok": False, "error": "e_id required"}), 400
    e_name = str(data.get("e_name", ""))
    e_dept = str(data.get("e_dept", ""))
    reason = str(data.get("reason", "연차"))
    conn = _conn(); cur = conn.cursor()
    try:
        if action == "add":
            cur.execute("""
                INSERT INTO tbm_exclusions (week_id, e_id, e_name, e_dept, reason, created_by)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE reason=VALUES(reason)
            """, (week_id, e_id, e_name, e_dept, reason, session.get("user_name", "")))
        elif action == "remove":
            cur.execute("DELETE FROM tbm_exclusions WHERE week_id=%s AND e_id=%s", (week_id, e_id))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


@tbm_bp.route("/admin/week/<int:week_id>/close", methods=["POST"])
@_admin_required
def tbm_week_close(week_id):
    conn = _conn(); cur = conn.cursor()
    cur.execute("UPDATE tbm_weeks SET status='closed' WHERE id=%s", (week_id,))
    conn.commit(); conn.close()
    flash("주차가 마감됐습니다.", "success")
    return redirect(url_for("tbm.tbm_week_detail", week_id=week_id))


@tbm_bp.route("/admin/week/<int:week_id>/reopen", methods=["POST"])
@_admin_required
def tbm_week_reopen(week_id):
    conn = _conn(); cur = conn.cursor()
    cur.execute("UPDATE tbm_weeks SET status='open' WHERE id=%s", (week_id,))
    conn.commit(); conn.close()
    flash("주차가 재오픈됐습니다.", "info")
    return redirect(url_for("tbm.tbm_week_detail", week_id=week_id))


@tbm_bp.route("/admin/week/<int:week_id>/delete", methods=["POST"])
@_admin_required
def tbm_week_delete(week_id):
    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM tbm_sessions WHERE week_id=%s", (week_id,))
        session_ids = [r[0] for r in cur.fetchall()]
        for sid in session_ids:
            cur.execute("DELETE FROM tbm_attendance WHERE session_id=%s", (sid,))
            cur.execute("DELETE FROM tbm_hazards WHERE session_id=%s", (sid,))
        cur.execute("DELETE FROM tbm_sessions WHERE week_id=%s", (week_id,))
        cur.execute("DELETE FROM tbm_exclusions WHERE week_id=%s", (week_id,))
        cur.execute("DELETE FROM tbm_weeks WHERE id=%s", (week_id,))
        conn.commit()
        flash("주차가 삭제됐습니다.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"삭제 실패: {e}", "danger")
    finally:
        conn.close()
    return redirect(url_for("tbm.tbm_dashboard"))


@tbm_bp.route("/api/week/<int:week_id>/stats")
@_admin_required
def tbm_api_stats(week_id):
    conn = _conn(); cur = conn.cursor()
    result = _week_stats(cur, week_id)
    conn.close()
    return jsonify(result)


@tbm_bp.route("/api/week/<int:week_id>/missing")
@_admin_required
def tbm_api_missing(week_id):
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT tbm_date FROM tbm_weeks WHERE id=%s", (week_id,))
    row = cur.fetchone()
    ref_date = row[0] if row else None
    missing_all = {}
    for dept in TBM_DEPTS:
        emps = _get_employees_by_dept(dept, ref_date)
        auto_excl = _auto_excl_from_schedule(emps, ref_date)
        cur.execute("SELECT id FROM tbm_sessions WHERE week_id=%s AND dept=%s", (week_id, dept))
        sess = cur.fetchone()
        signed_ids = set()
        if sess:
            cur.execute("SELECT e_id FROM tbm_attendance WHERE session_id=%s", (sess[0],))
            signed_ids = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT e_id FROM tbm_exclusions WHERE week_id=%s AND e_dept=%s", (week_id, dept))
        manual_excl_ids = {r[0] for r in cur.fetchall()}
        all_excl_ids = set(auto_excl.keys()) | manual_excl_ids
        missing_all[dept] = [e for e in emps if e["e_id"] not in signed_ids and e["e_id"] not in all_excl_ids]
    conn.close()
    return jsonify(missing_all)


@tbm_bp.route("/api/employees/<dept>")
@_admin_required
def tbm_api_employees(dept):
    return jsonify(_get_employees_by_dept(dept))


@tbm_bp.route("/sign")
@_login_required
def tbm_sign():
    e_id_tuser = session.get("e_id")
    if not e_id_tuser:
        flash("직원 정보가 없습니다. 관리자에게 문의하세요.", "warning")
        return render_template("tbm/sign.html", session_data=None, user_name=session.get("user_name"))
    er_row = _get_er_id(e_id_tuser)
    e_id = er_row[0] if er_row else e_id_tuser
    dept = _get_user_dept(e_id_tuser)
    if not dept:
        flash("TBM 대상 사업부가 아닙니다.", "info")
        return render_template("tbm/sign.html", session_data=None, dept=None, user_name=session.get("user_name"))
    conn = _conn(); cur = conn.cursor()
    cur.execute("""
        SELECT s.id, s.job_name, s.job_content, s.location,
               s.key_hazard_select, s.key_hazard_measure,
               s.safety_instruction, s.time_start, s.time_end, s.same_as_job_date,
               s.risk_assessed, s.leader_dept, s.leader_name, s.leader_position,
               s.safety_check1, s.safety_check2, s.safety_check3,
               w.id, w.year, w.week_no, w.tbm_date, w.week_label, w.status
        FROM tbm_sessions s
        JOIN tbm_weeks w ON s.week_id = w.id
        WHERE s.dept=%s AND w.status='open'
        ORDER BY w.year DESC, w.week_no DESC LIMIT 1
    """, (dept,))
    row = cur.fetchone()
    if not row:
        conn.close()
        flash("현재 참여 가능한 TBM이 없습니다.", "info")
        return render_template("tbm/sign.html", session_data=None, dept=dept, user_name=session.get("user_name"))
    session_id = row[0]
    week_id    = row[17]
    cur.execute("SELECT signed_at FROM tbm_attendance WHERE session_id=%s AND e_id=%s", (session_id, e_id))
    already = cur.fetchone()
    cur.execute("SELECT reason FROM tbm_exclusions WHERE week_id=%s AND e_id=%s", (week_id, e_id))
    excl = cur.fetchone()
    cur.execute(
        "SELECT hazard, measure FROM tbm_hazards WHERE session_id=%s ORDER BY sort_order",
        (session_id,)
    )
    hazards = [{"hazard": r[0], "measure": r[1]} for r in cur.fetchall()]
    conn.close()
    e_name_for_excl = session.get("user_name", "")
    tbm_date_str = str(row[20]).replace("-", "")
    sched_statuses = _get_schedule_statuses([e_name_for_excl], tbm_date_str)
    auto_excl_etc = sched_statuses.get(e_name_for_excl, "")
    auto_excl_reason = TBM_EXCLUDE_ETC.get(auto_excl_etc)
    tmpl_map = _get_templates(dept)
    combined_tmpl = {**tmpl_map.get("공통", {}), **tmpl_map.get(dept, {})}
    tmpl_instruction = combined_tmpl.get("safety_instruction", "")
    sd = {
        "id": session_id,
        "job_name": row[1], "job_content": row[2], "location": row[3],
        "key_hazard_select": row[4], "key_hazard_measure": row[5],
        "safety_instruction": row[6],
        "time_start": row[7], "time_end": row[8], "same_as_job_date": row[9],
        "risk_assessed": row[10],
        "leader_dept": row[11], "leader_name": row[12], "leader_position": row[13],
        "safety_check1": row[14], "safety_check2": row[15], "safety_check3": row[16],
        "year": row[18], "week_no": row[19],
        "tbm_date": str(row[20]),
        "week_label": row[21] or _week_label(row[18], row[19]),
        "dept": dept, "hazards": hazards,
        "tmpl_instruction": tmpl_instruction,
    }
    is_excl = bool(excl) or bool(auto_excl_reason)
    excl_reason = (excl[0] if excl else None) or (f"[근무표] {auto_excl_reason}" if auto_excl_reason else None)
    return render_template(
        "tbm/sign.html",
        session_data=sd,
        already_signed=bool(already),
        signed_at=str(already[0]) if already else None,
        is_excluded=is_excl,
        excl_reason=excl_reason,
        user_name=session.get("user_name"),
        safety_items=SAFETY_ITEMS,
    )


@tbm_bp.route("/sign/submit", methods=["POST"])
@_login_required
def tbm_sign_submit():
    e_id_tuser = session.get("e_id")
    if not e_id_tuser:
        return jsonify({"ok": False, "error": "직원 정보 없음"}), 403
    er_row = _get_er_id(e_id_tuser)
    e_id  = er_row[0] if er_row else e_id_tuser
    emp_no = er_row[1] if er_row else ""
    data           = request.get_json(silent=True) or {}
    session_id     = int(data.get("session_id", 0))
    signature_data = str(data.get("signature_data", ""))
    agreed         = bool(data.get("agreed", False))
    if not session_id or not signature_data or not agreed:
        return jsonify({"ok": False, "error": "동의 및 서명이 필요합니다"}), 400
    conn = _conn(); cur = conn.cursor()
    cur.execute("""
        SELECT s.dept, w.status FROM tbm_sessions s
        JOIN tbm_weeks w ON s.week_id = w.id WHERE s.id=%s
    """, (session_id,))
    sess = cur.fetchone()
    if not sess:
        conn.close(); return jsonify({"ok": False, "error": "세션 없음"}), 404
    if sess[1] != "open":
        conn.close(); return jsonify({"ok": False, "error": "마감된 주차입니다"}), 403
    dept = _get_user_dept(e_id_tuser)
    if sess[0] != dept:
        conn.close(); return jsonify({"ok": False, "error": "사업부 불일치"}), 403
    cur.execute("SELECT tbm_date FROM tbm_weeks WHERE id=(SELECT week_id FROM tbm_sessions WHERE id=%s)",
                (session_id,))
    wk_date_row = cur.fetchone()
    if wk_date_row:
        tbm_date_str = str(wk_date_row[0]).replace("-", "")
        sched_st = _get_schedule_statuses([session.get("user_name", "")], tbm_date_str)
        etc_val = sched_st.get(session.get("user_name", ""), "")
        if TBM_EXCLUDE_ETC.get(etc_val):
            conn.close()
            return jsonify({"ok": False, "error": f"근무표 제외 대상({TBM_EXCLUDE_ETC[etc_val]})"}), 403
    e_name = session.get("user_name", "")
    try:
        cur.execute("""
            INSERT INTO tbm_attendance (session_id, e_id, emp_no, e_name, e_dept, signature_data)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE signature_data=VALUES(signature_data), signed_at=NOW()
        """, (session_id, e_id, emp_no, e_name, dept, signature_data))
        conn.commit()
        return jsonify({"ok": True, "message": "TBM 서명이 완료됐습니다."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


@tbm_bp.route("/sign/history")
@_login_required
def tbm_sign_history():
    e_id_tuser = session.get("e_id")
    er_row = _get_er_id(e_id_tuser) if e_id_tuser else None
    e_id = er_row[0] if er_row else e_id_tuser
    conn = _conn(); cur = conn.cursor()
    cur.execute("""
        SELECT a.e_dept, a.signed_at, w.year, w.week_no, w.week_label, w.tbm_date, s.job_name
        FROM tbm_attendance a
        JOIN tbm_sessions s ON a.session_id = s.id
        JOIN tbm_weeks w ON s.week_id = w.id
        WHERE a.e_id=%s
        ORDER BY a.signed_at DESC LIMIT 30
    """, (e_id,))
    rows = cur.fetchall()
    conn.close()
    history = [{
        "dept": r[0], "signed_at": str(r[1]),
        "year": r[2], "week_no": r[3],
        "label": r[4] or _week_label(r[2], r[3]),
        "tbm_date": str(r[5]), "job_name": r[6],
    } for r in rows]
    return render_template("tbm/history.html", history=history, user_name=session.get("user_name"))


@tbm_bp.route("/print/<int:week_id>")
@_admin_required
def tbm_print(week_id):
    conn = _conn(); cur = conn.cursor()
    cur.execute(
        "SELECT id, year, week_no, week_label, tbm_date, status FROM tbm_weeks WHERE id=%s",
        (week_id,)
    )
    wk = cur.fetchone()
    if not wk:
        flash("주차 없음.", "danger"); conn.close()
        return redirect(url_for("tbm.tbm_dashboard"))
    week = {"id": wk[0], "year": wk[1], "week_no": wk[2],
            "label": wk[3] or _week_label(wk[1], wk[2]),
            "tbm_date": wk[4], "status": wk[5]}
    ref_date = wk[4]
    print_sessions = {}
    for dept in TBM_DEPTS:
        cur.execute("SELECT * FROM tbm_sessions WHERE week_id=%s AND dept=%s", (week_id, dept))
        row = cur.fetchone()
        if not row: continue
        cols = [d[0] for d in cur.description]
        sess = dict(zip(cols, row))
        cur.execute("SELECT hazard, measure FROM tbm_hazards WHERE session_id=%s ORDER BY sort_order",
                    (sess["id"],))
        sess["hazards"] = [{"hazard": r[0], "measure": r[1]} for r in cur.fetchall()]
        cur.execute(
            "SELECT e_name, e_dept, signed_at, signature_data "
            "FROM tbm_attendance WHERE session_id=%s ORDER BY e_name",
            (sess["id"],)
        )
        sess["signed_list"] = [
            {"e_name": r[0], "e_dept": r[1], "signed_at": str(r[2]), "signature_data": r[3]}
            for r in cur.fetchall()
        ]
        cur.execute("SELECT e_name, reason FROM tbm_exclusions WHERE week_id=%s AND e_dept=%s", (week_id, dept))
        manual_excl_list = [{"e_name": r[0], "reason": r[1]} for r in cur.fetchall()]
        emps = _get_employees_by_dept(dept, ref_date)
        auto_excl = _auto_excl_from_schedule(emps, ref_date)
        auto_excl_names = {v["e_name"] for v in auto_excl.values()}
        manual_excl_names = {e["e_name"] for e in manual_excl_list}
        auto_only = [
            {"e_name": v["e_name"], "reason": v["reason"]}
            for v in auto_excl.values() if v["e_name"] not in manual_excl_names
        ]
        sess["excluded_list"] = manual_excl_list + auto_only
        sess["total_workers"] = len(emps) - len(auto_excl) - len(manual_excl_list)
        sess["dept"] = dept
        print_sessions[dept] = sess
    conn.close()
    return render_template(
        "tbm/print.html",
        week=week, sessions=print_sessions,
        tbm_depts=TBM_DEPTS, safety_items=SAFETY_ITEMS,
        tbm_defaults=TBM_DEFAULTS,
        print_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


@tbm_bp.route("/summary/<int:year>/<int:month>")
@_admin_required
def tbm_monthly_summary(year, month):
    conn = _conn(); cur = conn.cursor()
    cur.execute(
        "SELECT id, year, week_no, week_label, tbm_date, status "
        "FROM tbm_weeks WHERE year=%s "
        "AND MONTH(tbm_date)=%s AND status IN ('open','closed') ORDER BY week_no",
        (year, month)
    )
    weeks = []
    for wk in cur.fetchall():
        wid = wk[0]
        ref_date = wk[4]
        row = {"id": wid, "year": wk[1], "week_no": wk[2],
               "label": wk[3] or _week_label(wk[1], wk[2]),
               "tbm_date": wk[4], "status": wk[5], "depts": {}}
        for dept in TBM_DEPTS:
            cur.execute("SELECT id, key_hazard_select, key_hazard_measure FROM tbm_sessions "
                        "WHERE week_id=%s AND dept=%s", (wid, dept))
            sess = cur.fetchone()
            emps = _get_employees_by_dept(dept, ref_date)
            auto_excl = _auto_excl_from_schedule(emps, ref_date)
            total = len(emps)
            signed = 0
            if sess:
                cur.execute("SELECT COUNT(*) FROM tbm_attendance WHERE session_id=%s", (sess[0],))
                signed = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM tbm_exclusions WHERE week_id=%s AND e_dept=%s",
                        (wid, dept))
            manual_excl_cnt = cur.fetchone()[0]
            excluded = len(auto_excl) + manual_excl_cnt
            row["depts"][dept] = {
                "key_hazard": f"{sess[1] or ''} / {sess[2] or ''}" if sess else "",
                "total": total, "excluded": excluded, "signed": signed,
            }
        weeks.append(row)
    conn.close()
    return render_template("tbm/summary.html", weeks=weeks, year=year, month=month,
                           tbm_depts=TBM_DEPTS, user_name=session.get("user_name"))


TEMPLATE_KEYS = [
    ("job_name",             "작업명"),
    ("job_content",          "작업내용"),
    ("location",             "TBM장소"),
    ("leader_dept",          "TBM리더 확인"),
]
EXTRA_TEMPLATE_KEYS = ["leader_name", "leader_position"]


@tbm_bp.route("/admin/templates")
@_admin_required
def tbm_template_mgmt():
    all_tmpl = _get_templates()
    manager_dept_options = _get_manager_dept_options()
    return render_template(
        "tbm/templates_mgmt.html",
        all_tmpl=all_tmpl,
        tmpl_keys=TEMPLATE_KEYS,
        tbm_depts=TBM_DEPTS + ["공통"],
        manager_dept_options=manager_dept_options,
        user_name=session.get("user_name"),
    )


@tbm_bp.route("/admin/templates/save", methods=["POST"])
@_admin_required
def tbm_template_save():
    conn = _conn(); cur = conn.cursor()
    try:
        for dept in TBM_DEPTS + ["공통"]:
            for tkey in [k for k, _ in TEMPLATE_KEYS] + EXTRA_TEMPLATE_KEYS:
                content = request.form.get(f"{dept}__{tkey}", "").strip()
                cur.execute("""
                    INSERT INTO tbm_templates (dept, template_key, content, updated_by)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE content=VALUES(content), updated_by=VALUES(updated_by)
                """, (dept, tkey, content, session.get("user_name", "")))
        conn.commit()
        flash("템플릿이 저장됐습니다.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"저장 오류: {e}", "danger")
    finally:
        conn.close()
    return redirect(url_for("tbm.tbm_template_mgmt"))
