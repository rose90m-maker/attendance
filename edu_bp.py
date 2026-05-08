"""교육/훈련 관리 Blueprint"""
import os
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, jsonify, session, redirect, flash, send_from_directory, abort
from werkzeug.utils import secure_filename
import pymysql

EDU_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", "education")
os.makedirs(EDU_UPLOAD_DIR, exist_ok=True)
ALLOWED_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.pdf', '.pptx', '.ppt', '.xlsx', '.xls', '.docx', '.doc', '.zip'}

edu_bp = Blueprint("edu", __name__, url_prefix="/education")

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
            flash("관리자 전용입니다.", "danger"); return redirect("/")
        return f(*a, **kw)
    return deco


def init_edu_db(app):
    with app.app_context():
        conn = _conn(); cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `edu_courses` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `name` VARCHAR(100) NOT NULL,
                `cycle_type` ENUM('year','half','quarter') NOT NULL DEFAULT 'year',
                `required_hours` DECIMAL(4,1) NOT NULL DEFAULT 1.0,
                `target` VARCHAR(50) DEFAULT 'all',
                `is_legal` TINYINT(1) DEFAULT 1,
                `is_active` TINYINT(1) DEFAULT 1,
                `note` TEXT,
                `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `edu_sessions` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `course_id` INT NOT NULL,
                `title` VARCHAR(200),
                `edu_date` DATE NOT NULL,
                `hours` DECIMAL(4,1) DEFAULT 1.0,
                `location` VARCHAR(100),
                `instructor` VARCHAR(50),
                `note` TEXT,
                `created_by` VARCHAR(50),
                `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX `idx_es_course` (`course_id`),
                INDEX `idx_es_date` (`edu_date`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `edu_completions` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `session_id` INT NOT NULL,
                `employee_name` VARCHAR(50) NOT NULL,
                `dept` VARCHAR(50) DEFAULT '',
                `file_path` VARCHAR(500),
                `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX `idx_ec_session` (`session_id`),
                INDEX `idx_ec_name` (`employee_name`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `edu_files` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `session_id` INT NOT NULL,
                `orig_name` VARCHAR(300) NOT NULL,
                `save_name` VARCHAR(300) NOT NULL,
                `file_size` INT DEFAULT 0,
                `uploaded_by` VARCHAR(50),
                `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX `idx_ef_session` (`session_id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        # penalty 컬럼 추가 (없으면)
        try:
            cur.execute("ALTER TABLE edu_courses ADD COLUMN `penalty` TEXT AFTER `note`")
            conn.commit()
        except Exception:
            pass  # 이미 존재

        # 기본 법정교육 데이터
        cur.execute("SELECT COUNT(*) FROM edu_courses")
        if cur.fetchone()[0] == 0:
            defaults = [
                ("산업안전보건교육", "quarter", 6.0, "all", 1, "분기별 6시간 (사무직 3시간)",
                 "산업재해 예방을 위해 안전보건에 관한 지식과 의식을 높이는 교육입니다. 기계·화학·추락 등 사고 위험 감소와 안전한 작업 환경 조성이 목적입니다. 📌 근거: 산업안전보건법 제29조 | ⚠️ 미실시 시 500만원 이하 과태료 (제175조), 상시근로자 1인 이상 전 사업장 의무."),
                ("성희롱 예방교육", "year", 1.0, "all", 1, "연 1회 이상",
                 "직장 내 성희롱 발생을 예방하고 피해자 보호·구제 방법을 알리는 교육입니다. 건강한 직장 문화 조성과 성희롱 발생 시 대처 절차를 숙지하는 것이 목적입니다. 📌 근거: 남녀고용평등법 제13조 | ⚠️ 미실시 시 500만원 이하 과태료 (제39조), 전 사업장 연 1회 이상 의무."),
                ("개인정보보호교육", "year", 1.0, "all", 1, "연 1회 이상",
                 "임직원이 업무 중 취급하는 고객·직원 개인정보를 안전하게 관리하도록 하는 교육입니다. 정보 유출·오남용 방지와 개인정보보호법 준수가 목적입니다. 📌 근거: 개인정보보호법 제28조 | ⚠️ 미실시·관리 소홀 시 최대 3,000만원 이하 과태료 (제75조), 개인정보 취급자 전원 대상."),
                ("소방훈련/교육", "half", 1.0, "all", 1, "연 2회 이상",
                 "화재 발생 시 신속한 대피·초기진화 능력을 갖추기 위한 실습 중심 교육입니다. 소화기·옥내소화전 사용법, 피난 경로 숙지, 비상연락 체계 확인이 목적입니다. 📌 근거: 화재예방법 제37조 | ⚠️ 미실시 시 200만원 이하 과태료 (제52조), 상시 10인 이상 사업장 연 2회 이상 의무."),
                ("직장 내 장애인 인식개선교육", "year", 1.0, "all", 1, "연 1회 이상",
                 "장애인에 대한 편견을 해소하고 장애인 근로자와의 원활한 협업 환경을 만들기 위한 교육입니다. 장애 유형별 이해, 차별 금지, 편의 제공 방법 습득이 목적입니다. 📌 근거: 장애인고용촉진법 제5조의2 | ⚠️ 미실시 시 300만원 이하 과태료 (제86조), 상시 1인 이상 전 사업장 연 1회 의무."),
                ("퇴직연금교육", "year", 1.0, "all", 1, "연 1회 이상",
                 "근로자가 퇴직연금 제도의 구조·운용·수익률 등을 이해하고 노후 자산을 스스로 관리할 수 있도록 돕는 교육입니다. DC형 가입자의 경우 투자 성향에 맞는 운용지시 능력 향상이 목적입니다. 📌 근거: 근로자퇴직급여보장법 제32조 | ⚠️ 미실시 시 1,000만원 이하 과태료 (제48조), 퇴직연금 가입 사업장 연 1회 의무."),
                ("직장 내 괴롭힘 예방교육", "year", 1.0, "all", 1, "연 1회 이상",
                 "직장 내 괴롭힘(갑질·따돌림·모욕 등)의 개념과 예방법, 피해 시 신고·처리 절차를 안내하는 교육입니다. 심리적으로 안전한 조직 문화 조성과 2차 피해 방지가 목적입니다. 📌 근거: 근로기준법 제76조의3 | ⚠️ 직접 과태료 조항은 없으나 예방 의무 위반 시 사용자 손해배상 책임 발생 가능, 취업규칙 기재 의무."),
            ]
            for d in defaults:
                cur.execute("""INSERT INTO edu_courses (name,cycle_type,required_hours,target,is_legal,note,penalty)
                               VALUES (%s,%s,%s,%s,%s,%s,%s)""", d)
        else:
            # 기존 데이터에 penalty 업데이트
            penalties = {
                "산업안전보건교육": (
                    "산업재해 예방을 위해 안전보건에 관한 지식과 의식을 높이는 교육입니다. "
                    "기계·화학·추락 등 사고 위험 감소와 안전한 작업 환경 조성이 목적입니다. "
                    "📌 근거: 산업안전보건법 제29조 | "
                    "⚠️ 미실시 시 500만원 이하 과태료 (제175조), 상시근로자 1인 이상 전 사업장 의무."
                ),
                "성희롱 예방교육": (
                    "직장 내 성희롱 발생을 예방하고 피해자 보호·구제 방법을 알리는 교육입니다. "
                    "건강한 직장 문화 조성과 성희롱 발생 시 대처 절차를 숙지하는 것이 목적입니다. "
                    "📌 근거: 남녀고용평등법 제13조 | "
                    "⚠️ 미실시 시 500만원 이하 과태료 (제39조), 전 사업장 연 1회 이상 의무."
                ),
                "개인정보보호교육": (
                    "임직원이 업무 중 취급하는 고객·직원 개인정보를 안전하게 관리하도록 하는 교육입니다. "
                    "정보 유출·오남용 방지와 개인정보보호법 준수가 목적입니다. "
                    "📌 근거: 개인정보보호법 제28조 | "
                    "⚠️ 미실시·관리 소홀 시 최대 3,000만원 이하 과태료 (제75조), 개인정보 취급자 전원 대상."
                ),
                "소방훈련/교육": (
                    "화재 발생 시 신속한 대피·초기진화 능력을 갖추기 위한 실습 중심 교육입니다. "
                    "소화기·옥내소화전 사용법, 피난 경로 숙지, 비상연락 체계 확인이 목적입니다. "
                    "📌 근거: 화재예방법 제37조 | "
                    "⚠️ 미실시 시 200만원 이하 과태료 (제52조), 상시 10인 이상 사업장 연 2회 이상 의무."
                ),
                "직장 내 장애인 인식개선교육": (
                    "장애인에 대한 편견을 해소하고 장애인 근로자와의 원활한 협업 환경을 만들기 위한 교육입니다. "
                    "장애 유형별 이해, 차별 금지, 편의 제공 방법 습득이 목적입니다. "
                    "📌 근거: 장애인고용촉진법 제5조의2 | "
                    "⚠️ 미실시 시 300만원 이하 과태료 (제86조), 상시 1인 이상 전 사업장 연 1회 의무."
                ),
                "퇴직연금교육": (
                    "근로자가 퇴직연금 제도의 구조·운용·수익률 등을 이해하고 노후 자산을 스스로 관리할 수 있도록 돕는 교육입니다. "
                    "DC형 가입자의 경우 투자 성향에 맞는 운용지시 능력 향상이 목적입니다. "
                    "📌 근거: 근로자퇴직급여보장법 제32조 | "
                    "⚠️ 미실시 시 1,000만원 이하 과태료 (제48조), 퇴직연금 가입 사업장 연 1회 의무."
                ),
                "직장 내 괴롭힘 예방교육": (
                    "직장 내 괴롭힘(갑질·따돌림·모욕 등)의 개념과 예방법, 피해 시 신고·처리 절차를 안내하는 교육입니다. "
                    "심리적으로 안전한 조직 문화 조성과 2차 피해 방지가 목적입니다. "
                    "📌 근거: 근로기준법 제76조의3 | "
                    "⚠️ 직접 과태료 조항은 없으나 예방 의무 위반 시 사용자 손해배상 책임 발생 가능, 취업규칙 기재 의무."
                ),
            }
            for name, penalty in penalties.items():
                cur.execute("UPDATE edu_courses SET penalty=%s WHERE name=%s",
                            (penalty, name))
        conn.commit(); conn.close()


def _get_year_range(cycle_type):
    """주기 타입별 현재 기준 시작/종료 날짜 반환"""
    today = date.today()
    if cycle_type == 'year':
        return date(today.year, 1, 1), date(today.year, 12, 31)
    elif cycle_type == 'half':
        if today.month <= 6:
            return date(today.year, 1, 1), date(today.year, 6, 30)
        else:
            return date(today.year, 7, 1), date(today.year, 12, 31)
    elif cycle_type == 'quarter':
        q = (today.month - 1) // 3
        starts = [date(today.year, 1, 1), date(today.year, 4, 1),
                  date(today.year, 7, 1), date(today.year, 10, 1)]
        ends   = [date(today.year, 3, 31), date(today.year, 6, 30),
                  date(today.year, 9, 30), date(today.year, 12, 31)]
        return starts[q], ends[q]
    return date(today.year, 1, 1), date(today.year, 12, 31)


@edu_bp.route("/")
@_login_required
def edu_main():
    conn = _conn(); cur = conn.cursor()
    today = date.today()

    # 전체 직원 목록
    cur.execute("""SELECT name, COALESCE(dept,'') FROM employee_roster
                   WHERE name IS NOT NULL AND name <> '' ORDER BY name""")
    employees = [{"name": r[0], "dept": r[1]} for r in cur.fetchall()]
    total_emp = len(employees)

    # 교육 과정 목록
    cur.execute("SELECT id,name,cycle_type,required_hours,is_legal,COALESCE(penalty,'') FROM edu_courses WHERE is_active=1 ORDER BY is_legal DESC,id")
    courses = cur.fetchall()

    summary = []
    for c in courses:
        cid, cname, cycle_type, req_hours, is_legal, penalty = c
        start, end = _get_year_range(cycle_type)

        # 해당 기간 이수자
        cur.execute("""SELECT DISTINCT ec.employee_name
                       FROM edu_completions ec
                       JOIN edu_sessions es ON es.id=ec.session_id
                       WHERE es.course_id=%s AND es.edu_date BETWEEN %s AND %s""",
                    (cid, start, end))
        completed_names = {r[0] for r in cur.fetchall()}
        completed = len(completed_names)
        not_completed = [e for e in employees if e["name"] not in completed_names]
        rate = round(completed / total_emp * 100) if total_emp else 0

        # 다음 예정 세션
        cur.execute("""SELECT edu_date, title FROM edu_sessions
                       WHERE course_id=%s AND edu_date >= %s ORDER BY edu_date LIMIT 1""",
                    (cid, today))
        next_sess = cur.fetchone()

        # penalty 텍스트를 | 기준으로 분리
        parts = [p.strip() for p in penalty.split("|")] if penalty else []
        penalty_desc   = parts[0] if len(parts) > 0 else ""
        penalty_basis  = parts[1] if len(parts) > 1 else ""
        penalty_fine   = parts[2] if len(parts) > 2 else ""

        summary.append({
            "id": cid, "name": cname, "cycle_type": cycle_type,
            "is_legal": is_legal,
            "penalty_desc": penalty_desc, "penalty_basis": penalty_basis, "penalty_fine": penalty_fine,
            "start": start, "end": end,
            "completed": completed, "total": total_emp, "rate": rate,
            "not_completed": not_completed[:5],
            "not_completed_count": len(not_completed),
            "next_session": next_sess[0].strftime("%Y-%m-%d") if next_sess else None,
            "next_title": next_sess[1] if next_sess else None,
        })

    conn.close()
    cycle_label = {"year": "연간", "half": "반기", "quarter": "분기"}
    return render_template("education.html", summary=summary, total_emp=total_emp,
                           cycle_label=cycle_label, active_page="education")


@edu_bp.route("/courses")
@_admin_required
def edu_courses():
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT id,name,cycle_type,required_hours,target,is_legal,is_active,note FROM edu_courses ORDER BY is_legal DESC,id")
    courses = [{"id":r[0],"name":r[1],"cycle_type":r[2],"required_hours":r[3],
                "target":r[4],"is_legal":bool(r[5]),"is_active":bool(r[6]),"note":r[7] or ""}
               for r in cur.fetchall()]
    conn.close()
    return render_template("education_courses.html", courses=courses, active_page="edu_courses")


@edu_bp.route("/sessions")
@_admin_required
def edu_sessions():
    year = request.args.get("year", date.today().year, type=int)
    conn = _conn(); cur = conn.cursor()
    cur.execute("""SELECT es.id, ec_name.name, es.title, es.edu_date, es.hours,
                          es.location, es.instructor,
                          (SELECT COUNT(*) FROM edu_completions WHERE session_id=es.id) AS cnt
                   FROM edu_sessions es
                   JOIN edu_courses ec_name ON ec_name.id=es.course_id
                   WHERE YEAR(es.edu_date)=%s
                   ORDER BY es.edu_date DESC""", (year,))
    sessions = [{"id":r[0],"course":r[1],"title":r[2] or r[1],
                 "edu_date":r[3].strftime("%Y-%m-%d"),"hours":r[4],
                 "location":r[5] or "","instructor":r[6] or "","cnt":r[7]}
                for r in cur.fetchall()]
    cur.execute("SELECT id,name FROM edu_courses WHERE is_active=1 ORDER BY id")
    courses = [{"id":r[0],"name":r[1]} for r in cur.fetchall()]
    conn.close()
    return render_template("education_sessions.html", sessions=sessions,
                           courses=courses, year=year, active_page="edu_sessions")


@edu_bp.route("/sessions/<int:sid>")
@_admin_required
def edu_session_detail(sid):
    conn = _conn(); cur = conn.cursor()
    cur.execute("""SELECT es.id, ec.name, es.title, es.edu_date, es.hours,
                          es.location, es.instructor, es.note
                   FROM edu_sessions es JOIN edu_courses ec ON ec.id=es.course_id
                   WHERE es.id=%s""", (sid,))
    row = cur.fetchone()
    if not row:
        conn.close(); flash("세션을 찾을 수 없습니다.", "danger"); return redirect("/education/sessions")
    sess = {"id":row[0],"course":row[1],"title":row[2] or row[1],
            "edu_date":row[3].strftime("%Y-%m-%d"),"hours":row[4],
            "location":row[5] or "","instructor":row[6] or "","note":row[7] or ""}
    cur.execute("""SELECT id, employee_name, dept, file_path, created_at
                   FROM edu_completions WHERE session_id=%s ORDER BY dept, employee_name""", (sid,))
    completions = [{"id":r[0],"name":r[1],"dept":r[2] or "","file":r[3],
                    "created_at":r[4].strftime("%Y-%m-%d") if r[4] else ""}
                   for r in cur.fetchall()]
    cur.execute("""SELECT name, COALESCE(dept,'') FROM employee_roster
                   WHERE name IS NOT NULL AND name <> '' ORDER BY dept, name""")
    employees = [{"name":r[0],"dept":r[1]} for r in cur.fetchall()]
    completed_names = {c["name"] for c in completions}
    not_completed = [e for e in employees if e["name"] not in completed_names]
    cur.execute("""SELECT id, orig_name, save_name, file_size, uploaded_by, created_at
                   FROM edu_files WHERE session_id=%s ORDER BY created_at""", (sid,))
    files = [{"id":r[0],"orig_name":r[1],"save_name":r[2],
              "file_size":r[3],"uploaded_by":r[4] or "",
              "created_at":r[5].strftime("%Y-%m-%d") if r[5] else ""}
             for r in cur.fetchall()]
    conn.close()
    return render_template("education_detail.html", sess=sess, completions=completions,
                           not_completed=not_completed, files=files, active_page="education")


# ── API ──────────────────────────────────────────────────────

@edu_bp.route("/api/course/save", methods=["POST"])
@_admin_required
def api_course_save():
    try:
        d = request.get_json(force=True)
        cid = d.get("id")
        name = d["name"].strip()
        cycle_type = d.get("cycle_type", "year")
        req_hours = float(d.get("required_hours", 1.0))
        target = d.get("target", "all")
        is_legal = int(d.get("is_legal", 1))
        note = d.get("note", "")
        conn = _conn(); cur = conn.cursor()
        if cid:
            cur.execute("""UPDATE edu_courses SET name=%s,cycle_type=%s,required_hours=%s,
                           target=%s,is_legal=%s,note=%s WHERE id=%s""",
                        (name, cycle_type, req_hours, target, is_legal, note, cid))
        else:
            cur.execute("""INSERT INTO edu_courses (name,cycle_type,required_hours,target,is_legal,note)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (name, cycle_type, req_hours, target, is_legal, note))
            cid = cur.lastrowid
        conn.commit(); conn.close()
        return jsonify(ok=True, id=cid)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@edu_bp.route("/api/course/toggle", methods=["POST"])
@_admin_required
def api_course_toggle():
    try:
        d = request.get_json(force=True)
        conn = _conn(); cur = conn.cursor()
        cur.execute("UPDATE edu_courses SET is_active=1-is_active WHERE id=%s", (d["id"],))
        conn.commit(); conn.close()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@edu_bp.route("/api/session/save", methods=["POST"])
@_admin_required
def api_session_save():
    try:
        d = request.get_json(force=True)
        sid = d.get("id")
        conn = _conn(); cur = conn.cursor()
        if sid:
            cur.execute("""UPDATE edu_sessions SET course_id=%s,title=%s,edu_date=%s,
                           hours=%s,location=%s,instructor=%s,note=%s WHERE id=%s""",
                        (d["course_id"],d.get("title",""),d["edu_date"],
                         float(d.get("hours",1)),d.get("location",""),
                         d.get("instructor",""),d.get("note",""),sid))
        else:
            cur.execute("""INSERT INTO edu_sessions (course_id,title,edu_date,hours,location,instructor,note,created_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (d["course_id"],d.get("title",""),d["edu_date"],
                         float(d.get("hours",1)),d.get("location",""),
                         d.get("instructor",""),d.get("note",""),
                         session.get("user_name","")))
            sid = cur.lastrowid
        conn.commit(); conn.close()
        return jsonify(ok=True, id=sid)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@edu_bp.route("/api/session/delete", methods=["POST"])
@_admin_required
def api_session_delete():
    try:
        d = request.get_json(force=True)
        conn = _conn(); cur = conn.cursor()
        cur.execute("DELETE FROM edu_completions WHERE session_id=%s", (d["id"],))
        cur.execute("DELETE FROM edu_sessions WHERE id=%s", (d["id"],))
        conn.commit(); conn.close()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@edu_bp.route("/api/completion/add", methods=["POST"])
@_admin_required
def api_completion_add():
    try:
        d = request.get_json(force=True)
        session_id = d["session_id"]
        names = d.get("names", [])
        conn = _conn(); cur = conn.cursor()
        # 직원 부서 조회
        cur.execute("SELECT name, COALESCE(dept,'') FROM employee_roster")
        dept_map = {r[0]: r[1] for r in cur.fetchall()}
        added = 0
        for name in names:
            cur.execute("SELECT id FROM edu_completions WHERE session_id=%s AND employee_name=%s",
                        (session_id, name))
            if not cur.fetchone():
                cur.execute("""INSERT INTO edu_completions (session_id,employee_name,dept)
                               VALUES (%s,%s,%s)""",
                            (session_id, name, dept_map.get(name, "")))
                added += 1
        conn.commit(); conn.close()
        return jsonify(ok=True, added=added)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@edu_bp.route("/api/completion/delete", methods=["POST"])
@_admin_required
def api_completion_delete():
    try:
        d = request.get_json(force=True)
        conn = _conn(); cur = conn.cursor()
        cur.execute("DELETE FROM edu_completions WHERE id=%s", (d["id"],))
        conn.commit(); conn.close()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@edu_bp.route("/api/file/upload", methods=["POST"])
@_admin_required
def api_file_upload():
    try:
        session_id = request.form.get("session_id", type=int)
        if not session_id:
            return jsonify(ok=False, error="session_id 없음"), 400
        files = request.files.getlist("files")
        if not files:
            return jsonify(ok=False, error="파일 없음"), 400
        conn = _conn(); cur = conn.cursor()
        saved = []
        for f in files:
            if not f or not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in ALLOWED_EXT:
                continue
            orig = f.filename
            safe = secure_filename(orig) or orig
            ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
            save_name = f"edu_{session_id}_{ts}_{safe}"
            f.save(os.path.join(EDU_UPLOAD_DIR, save_name))
            size = os.path.getsize(os.path.join(EDU_UPLOAD_DIR, save_name))
            cur.execute("""INSERT INTO edu_files (session_id, orig_name, save_name, file_size, uploaded_by)
                           VALUES (%s,%s,%s,%s,%s)""",
                        (session_id, orig, save_name, size, session.get("user_name", "")))
            saved.append({"id": cur.lastrowid, "orig_name": orig, "save_name": save_name,
                          "file_size": size})
        conn.commit(); conn.close()
        return jsonify(ok=True, files=saved)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@edu_bp.route("/files/<int:fid>")
@_login_required
def edu_file_download(fid):
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT orig_name, save_name FROM edu_files WHERE id=%s", (fid,))
    row = cur.fetchone()
    conn.close()
    if not row:
        abort(404)
    real = os.path.realpath(os.path.join(EDU_UPLOAD_DIR, row[1]))
    if not real.startswith(os.path.realpath(EDU_UPLOAD_DIR)):
        abort(403)
    return send_from_directory(EDU_UPLOAD_DIR, row[1], as_attachment=True,
                               download_name=row[0])


@edu_bp.route("/api/file/delete", methods=["POST"])
@_admin_required
def api_file_delete():
    try:
        d = request.get_json(force=True)
        conn = _conn(); cur = conn.cursor()
        cur.execute("SELECT save_name FROM edu_files WHERE id=%s", (d["id"],))
        row = cur.fetchone()
        if row:
            fpath = os.path.join(EDU_UPLOAD_DIR, row[0])
            if os.path.exists(fpath):
                os.remove(fpath)
            cur.execute("DELETE FROM edu_files WHERE id=%s", (d["id"],))
        conn.commit(); conn.close()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500
