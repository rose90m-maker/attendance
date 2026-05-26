"""㈜ 태인 — 디지털 사이니지 관리 (Phase 0 + 1.1)
Flask Blueprint. url_prefix=/signage, template_folder=signage.

Phase 0: DB 스키마 + 메뉴 + 진입점 (완료)
Phase 1.1: 콘텐츠 CRUD (현재)
Phase 1.2~: 플레이리스트 / 디스플레이 / 송출
"""
import os
import json
import uuid
import secrets
from pathlib import Path
from datetime import datetime
from functools import wraps

import pymysql
from dotenv import load_dotenv
from flask import (Blueprint, render_template, request, flash, jsonify,
                   redirect, url_for, session, send_from_directory,
                   current_app, abort)
from werkzeug.utils import secure_filename

load_dotenv()

signage_bp = Blueprint("signage", __name__, url_prefix="/signage",
                       template_folder="signage")


# ── 업로드 설정 ────────────────────────────────────────
UPLOAD_DIR = Path(os.environ.get("SIGNAGE_UPLOAD_DIR", "/app/uploads/signage"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTS = {
    'image': {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'},
    'video': {'mp4', 'webm', 'mov', 'mkv'},
    'pdf': {'pdf'},
}
MAX_UPLOAD_MB = 100


def _allowed_file(filename, kind):
    if '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in ALLOWED_EXTS.get(kind, set())


def _save_upload(file_storage, kind):
    """업로드 파일 저장 → 상대경로 반환"""
    if not file_storage or not file_storage.filename:
        return None
    if not _allowed_file(file_storage.filename, kind):
        raise ValueError(f"허용되지 않는 파일 형식: {file_storage.filename}")
    ext = file_storage.filename.rsplit('.', 1)[1].lower()
    fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.{ext}"
    fpath = UPLOAD_DIR / fname
    file_storage.save(str(fpath))
    return f"signage/{fname}"   # uploads/signage/xxx.png 형태로 URL 만듦


# ── DB 설정 (메인 앱과 동일 DB) ─────────────────────────
MARIA = {
    "host":     os.environ.get("DB_HOST", "127.0.0.1"),
    "port":     int(os.environ.get("DB_PORT", "3307")),
    "user":     os.environ.get("DB_USER", "root"),
    "password": os.environ["DB_PASSWORD"],
    "db":       os.environ.get("DB_NAME", "attendance"),
    "charset":  "utf8mb4",
}


def _conn():
    return pymysql.connect(**MARIA)


# ── 인증 데코레이터 ────────────────────────────────────
def _login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def _admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            flash("관리자 권한이 필요합니다.", "danger")
            return redirect(url_for("signage.dashboard"))
        return f(*args, **kwargs)
    return decorated


def _has_signage_perm(action="view"):
    """signage 권한 체크. 관리자는 통과. perm_groups 기반."""
    if session.get("role") == "admin":
        return True
    uid = session.get("user_id")
    if not uid:
        return False
    conn = _conn(); cur = conn.cursor()
    try:
        # perm_group 기반 권한
        cur.execute("""
            SELECT 1 FROM tuser u
            LEFT JOIN perm_groups g ON u.perm_group_id = g.id
            LEFT JOIN perm_group_actions gpa ON gpa.group_id = g.id
            WHERE u.id=%s AND gpa.menu_key='signage' AND gpa.action=%s
            LIMIT 1
        """, (uid, action))
        if cur.fetchone():
            return True
        # 개인 override
        cur.execute("""
            SELECT 1 FROM perm_user_overrides
            WHERE user_id=%s AND menu_key='signage' AND action=%s
            LIMIT 1
        """, (uid, action))
        return bool(cur.fetchone())
    except Exception:
        return False
    finally:
        conn.close()


# ── DB 초기화 (15개 테이블) ─────────────────────────────
def init_signage_db(cur):
    """앱 시작 시 호출 (idempotent — IF NOT EXISTS)"""

    # 1. 콘텐츠
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_contents (
            id INT AUTO_INCREMENT PRIMARY KEY,
            title VARCHAR(200) NOT NULL,
            type ENUM('image','video','pdf','web','text','table','mixed') NOT NULL DEFAULT 'text',
            body_text TEXT,
            file_path VARCHAR(500),
            web_url VARCHAR(500),
            thumbnail_path VARCHAR(500),
            duration_sec INT NOT NULL DEFAULT 10,
            start_at DATETIME NULL,
            end_at DATETIME NULL,
            priority INT NOT NULL DEFAULT 0,
            status ENUM('draft','active','paused') NOT NULL DEFAULT 'draft',
            created_by INT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            meta JSON NULL,
            INDEX idx_status (status),
            INDEX idx_period (start_at, end_at),
            INDEX idx_priority (priority DESC)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # 2. 태그
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_tags (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(50) UNIQUE NOT NULL,
            color VARCHAR(20) DEFAULT '#64748b',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # 3. 콘텐츠-태그 매핑
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_content_tags (
            content_id INT NOT NULL,
            tag_id INT NOT NULL,
            PRIMARY KEY (content_id, tag_id),
            INDEX idx_tag (tag_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # 4. 플레이리스트
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_playlists (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            description VARCHAR(500),
            loop_mode ENUM('sequential','random','priority') NOT NULL DEFAULT 'sequential',
            is_default TINYINT NOT NULL DEFAULT 0,
            status ENUM('active','paused') NOT NULL DEFAULT 'active',
            created_by INT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_default (is_default),
            INDEX idx_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # 5. 플레이리스트 항목
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_playlist_items (
            id INT AUTO_INCREMENT PRIMARY KEY,
            playlist_id INT NOT NULL,
            content_id INT NOT NULL,
            seq INT NOT NULL DEFAULT 0,
            duration_sec INT NULL,
            weight INT NOT NULL DEFAULT 1,
            INDEX idx_playlist (playlist_id, seq),
            INDEX idx_content (content_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # 6. 레이아웃
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_layouts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            template_type ENUM('full','split2','split3','top_bottom','overlay','slide','table','custom')
                NOT NULL DEFAULT 'full',
            resolution VARCHAR(20) NOT NULL DEFAULT '3840x2160',
            config JSON NULL,
            is_system TINYINT NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_sys_name (name, is_system)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    # 기존 테이블에 UNIQUE 보장 (idempotent)
    try:
        cur.execute("ALTER TABLE ds_layouts ADD UNIQUE KEY uq_sys_name (name, is_system)")
    except Exception:
        pass

    # 7. 레이아웃 영역
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_layout_zones (
            id INT AUTO_INCREMENT PRIMARY KEY,
            layout_id INT NOT NULL,
            zone_key VARCHAR(20) NOT NULL,
            x INT NOT NULL DEFAULT 0,
            y INT NOT NULL DEFAULT 0,
            w INT NOT NULL,
            h INT NOT NULL,
            z_index INT NOT NULL DEFAULT 0,
            INDEX idx_layout (layout_id),
            UNIQUE KEY uq_layout_zone (layout_id, zone_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    try:
        cur.execute("ALTER TABLE ds_layout_zones ADD UNIQUE KEY uq_layout_zone (layout_id, zone_key)")
    except Exception:
        pass

    # 8. 디스플레이 그룹
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_display_groups (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            description VARCHAR(200),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # 9. 디스플레이
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_displays (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL DEFAULT '',
            location VARCHAR(200),
            group_id INT NULL,
            default_playlist_id INT NULL,
            resolution VARCHAR(20) DEFAULT '3840x2160',
            orientation ENUM('landscape','portrait') NOT NULL DEFAULT 'landscape',
            token VARCHAR(64) UNIQUE,
            pair_code VARCHAR(8) NULL,
            pair_expires_at DATETIME NULL,
            paired TINYINT NOT NULL DEFAULT 0,
            last_seen DATETIME NULL,
            current_content_id INT NULL,
            status ENUM('online','offline','paused','error') NOT NULL DEFAULT 'offline',
            ip_address VARCHAR(50),
            user_agent VARCHAR(200),
            meta JSON NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_status (status),
            INDEX idx_group (group_id),
            INDEX idx_token (token),
            INDEX idx_pair_code (pair_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    # 기존 테이블 마이그레이션 (idempotent)
    for ddl in [
        "ALTER TABLE ds_displays ADD COLUMN default_playlist_id INT NULL AFTER group_id",
        "ALTER TABLE ds_displays ADD COLUMN paired TINYINT NOT NULL DEFAULT 0 AFTER pair_expires_at",
        "ALTER TABLE ds_displays MODIFY COLUMN name VARCHAR(100) NOT NULL DEFAULT ''",
        "ALTER TABLE ds_displays ADD INDEX idx_pair_code (pair_code)",
    ]:
        try: cur.execute(ddl)
        except Exception: pass

    # 10. 편성표
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_schedules (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            target_type ENUM('display','group','all') NOT NULL DEFAULT 'all',
            target_id INT NULL,
            layout_id INT NULL,
            playlist_id INT NULL,
            content_id INT NULL,
            start_date DATE NULL,
            end_date DATE NULL,
            start_time TIME NULL,
            end_time TIME NULL,
            recurrence ENUM('once','daily','weekly') NOT NULL DEFAULT 'daily',
            weekdays VARCHAR(20),
            exclude_holidays TINYINT NOT NULL DEFAULT 0,
            priority INT NOT NULL DEFAULT 0,
            status ENUM('active','paused') NOT NULL DEFAULT 'active',
            created_by INT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_target (target_type, target_id),
            INDEX idx_period (start_date, end_date),
            INDEX idx_priority (priority DESC),
            INDEX idx_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # 11. 긴급공지
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_emergency_messages (
            id INT AUTO_INCREMENT PRIMARY KEY,
            title VARCHAR(200) NOT NULL,
            body TEXT,
            image_path VARCHAR(500),
            target_type ENUM('all','group','display') NOT NULL DEFAULT 'all',
            target_ids JSON NULL,
            bg_color VARCHAR(20) DEFAULT '#dc2626',
            start_at DATETIME NULL,
            end_at DATETIME NULL,
            status ENUM('scheduled','active','ended') NOT NULL DEFAULT 'scheduled',
            created_by INT,
            ended_by INT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            ended_at DATETIME NULL,
            INDEX idx_status (status, start_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # 12. 재생 로그
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_play_logs (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            display_id INT NOT NULL,
            content_id INT NULL,
            playlist_id INT NULL,
            layout_id INT NULL,
            zone_key VARCHAR(20) NULL,
            started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ended_at DATETIME NULL,
            duration_sec INT NULL,
            INDEX idx_display_time (display_id, started_at),
            INDEX idx_content_time (content_id, started_at),
            INDEX idx_started (started_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # 13. 디스플레이 이벤트 로그
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_display_logs (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            display_id INT NOT NULL,
            event_type ENUM('online','offline','error','reload','version_check','paired') NOT NULL,
            message VARCHAR(500),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_display_time (display_id, created_at),
            INDEX idx_event (event_type, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # 14. 부서별 권한 확장
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_dept_perms (
            id INT AUTO_INCREMENT PRIMARY KEY,
            dept VARCHAR(100) NOT NULL,
            action ENUM('view','create','update','delete','publish') NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_dept_action (dept, action)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # 15. 디스플레이-그룹 다대다 (디스플레이 1개가 여러 그룹 소속 가능)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ds_display_group_map (
            display_id INT NOT NULL,
            group_id INT NOT NULL,
            PRIMARY KEY (display_id, group_id),
            INDEX idx_group (group_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # ── 기본 시스템 레이아웃 8종 시드 (idempotent, INSERT IGNORE) ──
    sys_layouts = [
        ('전체화면 1분할', 'full', '3840x2160',
            [('main', 0, 0, 3840, 2160, 0)]),
        ('좌우 2분할 60:40', 'split2', '3840x2160',
            [('left', 0, 0, 2304, 2160, 0), ('right', 2304, 0, 1536, 2160, 0)]),
        ('상단공지 + 메인', 'top_bottom', '3840x2160',
            [('top', 0, 0, 3840, 432, 1), ('main', 0, 432, 3840, 1728, 0)]),
        ('메인 + 하단자막', 'top_bottom', '3840x2160',
            [('main', 0, 0, 3840, 1944, 0), ('ticker', 0, 1944, 3840, 216, 1)]),
        ('3분할 대시보드', 'split3', '3840x2160',
            [('top', 0, 0, 3840, 432, 1), ('main', 0, 432, 2688, 1728, 0),
             ('side', 2688, 432, 1152, 1728, 0)]),
        ('영상 + 오버레이', 'overlay', '3840x2160',
            [('main', 0, 0, 3840, 2160, 0), ('overlay', 480, 1296, 2880, 432, 10)]),
        ('이미지 슬라이드', 'slide', '3840x2160',
            [('main', 0, 0, 3840, 2160, 0)]),
        ('표/현황판', 'table', '3840x2160',
            [('top', 0, 0, 3840, 432, 1), ('table', 0, 432, 3840, 1728, 0)]),
    ]
    for name, ttype, res, zones in sys_layouts:
        cur.execute("""INSERT IGNORE INTO ds_layouts (name, template_type, resolution, is_system)
                       VALUES (%s, %s, %s, 1)""", (name, ttype, res))
        cur.execute("SELECT id FROM ds_layouts WHERE name=%s AND is_system=1", (name,))
        lid_row = cur.fetchone()
        if not lid_row:
            continue
        lid = lid_row[0]
        for zk, x, y, w, h, z in zones:
            cur.execute("""INSERT IGNORE INTO ds_layout_zones
                           (layout_id, zone_key, x, y, w, h, z_index)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (lid, zk, x, y, w, h, z))


# ════════════════════════════════════════════════════════
#  라우트 — Phase 0 (대시보드 진입점만)
# ════════════════════════════════════════════════════════

@signage_bp.route("/")
@_login_required
def dashboard():
    """디지털 사이니지 메인 대시보드"""
    if not _has_signage_perm("view") and session.get("role") != "admin":
        flash("디지털 사이니지 접근 권한이 없습니다.", "danger")
        return redirect(url_for("dashboard"))

    conn = _conn(); cur = conn.cursor()
    stats = {}

    # 콘텐츠 수
    cur.execute("SELECT COUNT(*) FROM ds_contents WHERE status='active'")
    stats['content_active'] = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM ds_contents")
    stats['content_total'] = cur.fetchone()[0] or 0

    # 디스플레이 상태
    cur.execute("""SELECT status, COUNT(*) FROM ds_displays GROUP BY status""")
    disp_stats = {row[0]: row[1] for row in cur.fetchall()}
    stats['display_total'] = sum(disp_stats.values())
    stats['display_online'] = disp_stats.get('online', 0)
    stats['display_offline'] = disp_stats.get('offline', 0)

    # 활성 편성
    cur.execute("SELECT COUNT(*) FROM ds_schedules WHERE status='active'")
    stats['schedule_active'] = cur.fetchone()[0] or 0

    # 활성 긴급공지
    cur.execute("SELECT COUNT(*) FROM ds_emergency_messages WHERE status='active'")
    stats['emergency_active'] = cur.fetchone()[0] or 0

    # 오늘 재생 횟수
    cur.execute("""SELECT COUNT(*) FROM ds_play_logs
                   WHERE DATE(started_at) = CURDATE()""")
    stats['plays_today'] = cur.fetchone()[0] or 0

    # 디스플레이 목록 (요약)
    cur.execute("""SELECT id, name, location, status, last_seen, current_content_id
                   FROM ds_displays ORDER BY status='online' DESC, name
                   LIMIT 12""")
    displays = [
        {'id': r[0], 'name': r[1], 'location': r[2], 'status': r[3],
         'last_seen': r[4], 'current_content_id': r[5]}
        for r in cur.fetchall()
    ]

    # 최근 재생로그 (10개)
    cur.execute("""SELECT pl.started_at, d.name AS dname, c.title AS ctitle
                   FROM ds_play_logs pl
                   LEFT JOIN ds_displays d ON pl.display_id = d.id
                   LEFT JOIN ds_contents c ON pl.content_id = c.id
                   ORDER BY pl.started_at DESC LIMIT 10""")
    recent_logs = [
        {'time': r[0], 'display': r[1], 'content': r[2]}
        for r in cur.fetchall()
    ]

    conn.close()
    return render_template("signage/dashboard.html",
                           stats=stats, displays=displays, recent_logs=recent_logs,
                           is_admin=session.get('role') == 'admin')


# ════════════════════════════════════════════════════════
#  Phase 1.1 — 콘텐츠 CRUD
# ════════════════════════════════════════════════════════

@signage_bp.route("/contents")
@_login_required
def contents():
    """콘텐츠 목록 + 필터"""
    if not _has_signage_perm("view") and session.get("role") != "admin":
        flash("디지털 사이니지 접근 권한이 없습니다.", "danger")
        return redirect(url_for("dashboard"))

    # 필터 파라미터
    q_text = request.args.get('q', '').strip()
    f_type = request.args.get('type', '').strip()
    f_status = request.args.get('status', '').strip()
    f_tag = request.args.get('tag', '').strip()

    conn = _conn(); cur = conn.cursor()

    # 쿼리 빌드
    where = ["1=1"]
    params = []
    if q_text:
        where.append("(c.title LIKE %s OR c.body_text LIKE %s)")
        params.extend([f"%{q_text}%", f"%{q_text}%"])
    if f_type:
        where.append("c.type = %s")
        params.append(f_type)
    if f_status:
        where.append("c.status = %s")
        params.append(f_status)
    if f_tag:
        where.append("""EXISTS (SELECT 1 FROM ds_content_tags ct
                                JOIN ds_tags t ON ct.tag_id=t.id
                                WHERE ct.content_id=c.id AND t.name=%s)""")
        params.append(f_tag)

    sql = f"""SELECT c.id, c.title, c.type, c.body_text, c.file_path, c.web_url,
                     c.thumbnail_path, c.duration_sec, c.start_at, c.end_at,
                     c.priority, c.status, c.created_at, c.updated_at
              FROM ds_contents c
              WHERE {' AND '.join(where)}
              ORDER BY c.priority DESC, c.updated_at DESC
              LIMIT 200"""
    cur.execute(sql, params)
    rows = cur.fetchall()

    contents_list = []
    for r in rows:
        cid = r[0]
        # 태그
        cur.execute("""SELECT t.name, t.color FROM ds_tags t
                       JOIN ds_content_tags ct ON ct.tag_id=t.id
                       WHERE ct.content_id=%s""", (cid,))
        tags = [{'name': t[0], 'color': t[1]} for t in cur.fetchall()]
        contents_list.append({
            'id': r[0], 'title': r[1], 'type': r[2],
            'body_text': r[3], 'file_path': r[4], 'web_url': r[5],
            'thumbnail_path': r[6], 'duration_sec': r[7],
            'start_at': r[8], 'end_at': r[9],
            'priority': r[10], 'status': r[11],
            'created_at': r[12], 'updated_at': r[13],
            'tags': tags
        })

    # 전체 태그 목록 (필터용)
    cur.execute("SELECT name, color FROM ds_tags ORDER BY name")
    all_tags = [{'name': t[0], 'color': t[1]} for t in cur.fetchall()]

    # 카운트
    cur.execute("SELECT COUNT(*) FROM ds_contents")
    total_count = cur.fetchone()[0]

    conn.close()
    return render_template("signage/contents.html",
                           contents=contents_list,
                           all_tags=all_tags,
                           total_count=total_count,
                           filter_q=q_text, filter_type=f_type,
                           filter_status=f_status, filter_tag=f_tag,
                           is_admin=session.get('role') == 'admin',
                           can_edit=_has_signage_perm("create") or session.get('role') == 'admin')


@signage_bp.route("/contents/new", methods=["GET", "POST"])
@_login_required
def content_new():
    """콘텐츠 신규 등록"""
    if not _has_signage_perm("create") and session.get("role") != "admin":
        flash("등록 권한이 없습니다.", "danger")
        return redirect(url_for("signage.contents"))

    conn = _conn(); cur = conn.cursor()

    if request.method == "POST":
        title = request.form.get('title', '').strip()
        ctype = request.form.get('type', 'text')
        body_text = request.form.get('body_text', '').strip()
        web_url = request.form.get('web_url', '').strip()
        duration = int(request.form.get('duration_sec', 10))
        priority = int(request.form.get('priority', 0))
        status = request.form.get('status', 'draft')
        start_at = request.form.get('start_at') or None
        end_at = request.form.get('end_at') or None
        tag_names = [t.strip() for t in request.form.get('tags', '').split(',') if t.strip()]

        if not title:
            flash("제목을 입력하세요.", "danger")
            conn.close()
            return redirect(url_for("signage.content_new"))

        # 파일 업로드 처리 (image/video/pdf)
        file_path = None
        if ctype in ('image', 'video', 'pdf') and 'file' in request.files:
            try:
                file_path = _save_upload(request.files['file'], ctype)
            except ValueError as e:
                flash(str(e), "danger")
                conn.close()
                return redirect(url_for("signage.content_new"))

        try:
            cur.execute("""INSERT INTO ds_contents
                (title, type, body_text, file_path, web_url, duration_sec,
                 start_at, end_at, priority, status, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (title, ctype, body_text, file_path, web_url, duration,
                 start_at, end_at, priority, status, session.get('user_id')))
            cid = cur.lastrowid
            # 태그 처리
            for tname in tag_names:
                cur.execute("INSERT IGNORE INTO ds_tags (name) VALUES (%s)", (tname,))
                cur.execute("SELECT id FROM ds_tags WHERE name=%s", (tname,))
                tid = cur.fetchone()[0]
                cur.execute("INSERT IGNORE INTO ds_content_tags (content_id, tag_id) VALUES (%s, %s)", (cid, tid))
            conn.commit()
            flash(f"콘텐츠 '{title}' 등록 완료.", "success")
            conn.close()
            return redirect(url_for("signage.contents"))
        except Exception as e:
            conn.rollback()
            flash(f"등록 실패: {e}", "danger")

    # GET — 폼 표시
    cur.execute("SELECT name FROM ds_tags ORDER BY name")
    all_tags = [t[0] for t in cur.fetchall()]
    conn.close()
    return render_template("signage/content_form.html",
                           mode='new', content=None, all_tags=all_tags,
                           is_admin=session.get('role') == 'admin')


@signage_bp.route("/contents/<int:cid>/edit", methods=["GET", "POST"])
@_login_required
def content_edit(cid):
    """콘텐츠 편집"""
    if not _has_signage_perm("update") and session.get("role") != "admin":
        flash("편집 권한이 없습니다.", "danger")
        return redirect(url_for("signage.contents"))

    conn = _conn(); cur = conn.cursor()

    if request.method == "POST":
        title = request.form.get('title', '').strip()
        ctype = request.form.get('type', 'text')
        body_text = request.form.get('body_text', '').strip()
        web_url = request.form.get('web_url', '').strip()
        duration = int(request.form.get('duration_sec', 10))
        priority = int(request.form.get('priority', 0))
        status = request.form.get('status', 'draft')
        start_at = request.form.get('start_at') or None
        end_at = request.form.get('end_at') or None
        tag_names = [t.strip() for t in request.form.get('tags', '').split(',') if t.strip()]

        # 기존 파일 경로 유지 (새 업로드 없으면)
        cur.execute("SELECT file_path FROM ds_contents WHERE id=%s", (cid,))
        row = cur.fetchone()
        if not row:
            flash("존재하지 않는 콘텐츠입니다.", "danger")
            conn.close()
            return redirect(url_for("signage.contents"))
        file_path = row[0]

        if ctype in ('image', 'video', 'pdf') and 'file' in request.files and request.files['file'].filename:
            try:
                file_path = _save_upload(request.files['file'], ctype)
            except ValueError as e:
                flash(str(e), "danger")
                conn.close()
                return redirect(url_for("signage.content_edit", cid=cid))

        try:
            cur.execute("""UPDATE ds_contents SET
                title=%s, type=%s, body_text=%s, file_path=%s, web_url=%s,
                duration_sec=%s, start_at=%s, end_at=%s,
                priority=%s, status=%s
                WHERE id=%s""",
                (title, ctype, body_text, file_path, web_url,
                 duration, start_at, end_at, priority, status, cid))
            # 태그 전부 갱신
            cur.execute("DELETE FROM ds_content_tags WHERE content_id=%s", (cid,))
            for tname in tag_names:
                cur.execute("INSERT IGNORE INTO ds_tags (name) VALUES (%s)", (tname,))
                cur.execute("SELECT id FROM ds_tags WHERE name=%s", (tname,))
                tid = cur.fetchone()[0]
                cur.execute("INSERT IGNORE INTO ds_content_tags (content_id, tag_id) VALUES (%s, %s)", (cid, tid))
            conn.commit()
            flash("저장됨.", "success")
            conn.close()
            return redirect(url_for("signage.contents"))
        except Exception as e:
            conn.rollback()
            flash(f"저장 실패: {e}", "danger")

    # GET — 폼 표시
    cur.execute("""SELECT id, title, type, body_text, file_path, web_url,
                          duration_sec, start_at, end_at, priority, status
                   FROM ds_contents WHERE id=%s""", (cid,))
    r = cur.fetchone()
    if not r:
        conn.close()
        flash("존재하지 않는 콘텐츠입니다.", "danger")
        return redirect(url_for("signage.contents"))

    cur.execute("""SELECT t.name FROM ds_tags t
                   JOIN ds_content_tags ct ON ct.tag_id=t.id
                   WHERE ct.content_id=%s""", (cid,))
    content_tags = [t[0] for t in cur.fetchall()]

    content = {
        'id': r[0], 'title': r[1], 'type': r[2], 'body_text': r[3],
        'file_path': r[4], 'web_url': r[5], 'duration_sec': r[6],
        'start_at': r[7], 'end_at': r[8],
        'priority': r[9], 'status': r[10], 'tags': content_tags
    }

    cur.execute("SELECT name FROM ds_tags ORDER BY name")
    all_tags = [t[0] for t in cur.fetchall()]
    conn.close()
    return render_template("signage/content_form.html",
                           mode='edit', content=content, all_tags=all_tags,
                           is_admin=session.get('role') == 'admin')


@signage_bp.route("/contents/<int:cid>/delete", methods=["POST"])
@_login_required
def content_delete(cid):
    """콘텐츠 삭제"""
    if not _has_signage_perm("delete") and session.get("role") != "admin":
        return jsonify({"ok": False, "msg": "삭제 권한 없음"}), 403

    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM ds_content_tags WHERE content_id=%s", (cid,))
        cur.execute("DELETE FROM ds_contents WHERE id=%s", (cid,))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "msg": str(e)}), 500
    finally:
        conn.close()


@signage_bp.route("/contents/<int:cid>/preview")
@_login_required
def content_preview(cid):
    """콘텐츠 미리보기 (단독 페이지, 4K 비율 시뮬레이션)"""
    conn = _conn(); cur = conn.cursor()
    cur.execute("""SELECT id, title, type, body_text, file_path, web_url, duration_sec
                   FROM ds_contents WHERE id=%s""", (cid,))
    r = cur.fetchone()
    conn.close()
    if not r:
        abort(404)
    content = {
        'id': r[0], 'title': r[1], 'type': r[2], 'body_text': r[3],
        'file_path': r[4], 'web_url': r[5], 'duration_sec': r[6],
    }
    return render_template("signage/content_preview.html", content=content)


@signage_bp.route("/uploads/<path:fname>")
@_login_required
def serve_upload(fname):
    """업로드 파일 서빙 (signage/xxx.png 형태)"""
    base = UPLOAD_DIR.parent  # /app/uploads
    return send_from_directory(str(base), fname)


# ════════════════════════════════════════════════════════
#  Phase 1.2 — 플레이리스트 CRUD
# ════════════════════════════════════════════════════════

@signage_bp.route("/playlists")
@_login_required
def playlists():
    """플레이리스트 목록"""
    if not _has_signage_perm("view") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("dashboard"))

    conn = _conn(); cur = conn.cursor()
    cur.execute("""SELECT p.id, p.name, p.description, p.loop_mode, p.is_default,
                          p.status, p.created_at, p.updated_at,
                          (SELECT COUNT(*) FROM ds_playlist_items WHERE playlist_id=p.id) AS item_cnt,
                          (SELECT SUM(IFNULL(pi.duration_sec, c.duration_sec))
                           FROM ds_playlist_items pi
                           JOIN ds_contents c ON pi.content_id=c.id
                           WHERE pi.playlist_id=p.id) AS total_sec
                   FROM ds_playlists p
                   ORDER BY p.is_default DESC, p.updated_at DESC""")
    playlists = [
        {'id': r[0], 'name': r[1], 'description': r[2], 'loop_mode': r[3],
         'is_default': r[4], 'status': r[5], 'created_at': r[6], 'updated_at': r[7],
         'item_cnt': r[8] or 0, 'total_sec': r[9] or 0}
        for r in cur.fetchall()
    ]
    conn.close()
    return render_template("signage/playlists.html",
                           playlists=playlists,
                           is_admin=session.get('role') == 'admin',
                           can_edit=_has_signage_perm("create") or session.get('role') == 'admin')


@signage_bp.route("/playlists/new", methods=["GET", "POST"])
@_login_required
def playlist_new():
    """플레이리스트 신규 작성"""
    if not _has_signage_perm("create") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("signage.playlists"))

    conn = _conn(); cur = conn.cursor()

    if request.method == "POST":
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        loop_mode = request.form.get('loop_mode', 'sequential')
        is_default = 1 if request.form.get('is_default') else 0
        status = request.form.get('status', 'active')

        if not name:
            flash("이름을 입력하세요.", "danger")
            conn.close()
            return redirect(url_for("signage.playlist_new"))

        try:
            # 기본 플레이리스트는 1개만 허용
            if is_default:
                cur.execute("UPDATE ds_playlists SET is_default=0 WHERE is_default=1")
            cur.execute("""INSERT INTO ds_playlists
                (name, description, loop_mode, is_default, status, created_by)
                VALUES (%s, %s, %s, %s, %s, %s)""",
                (name, description, loop_mode, is_default, status, session.get('user_id')))
            pid = cur.lastrowid
            conn.commit()
            flash(f"플레이리스트 '{name}' 생성. 이어서 콘텐츠 추가 가능.", "success")
            conn.close()
            return redirect(url_for("signage.playlist_edit", pid=pid))
        except Exception as e:
            conn.rollback()
            flash(f"생성 실패: {e}", "danger")

    conn.close()
    return render_template("signage/playlist_form.html",
                           mode='new', playlist=None, items=[], library=[],
                           is_admin=session.get('role') == 'admin')


@signage_bp.route("/playlists/<int:pid>/edit", methods=["GET", "POST"])
@_login_required
def playlist_edit(pid):
    """플레이리스트 편집"""
    if not _has_signage_perm("update") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("signage.playlists"))

    conn = _conn(); cur = conn.cursor()

    if request.method == "POST":
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        loop_mode = request.form.get('loop_mode', 'sequential')
        is_default = 1 if request.form.get('is_default') else 0
        status = request.form.get('status', 'active')

        try:
            if is_default:
                cur.execute("UPDATE ds_playlists SET is_default=0 WHERE is_default=1 AND id<>%s", (pid,))
            cur.execute("""UPDATE ds_playlists SET
                name=%s, description=%s, loop_mode=%s, is_default=%s, status=%s
                WHERE id=%s""",
                (name, description, loop_mode, is_default, status, pid))
            conn.commit()
            flash("저장됨.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"저장 실패: {e}", "danger")
        conn.close()
        return redirect(url_for("signage.playlist_edit", pid=pid))

    # GET — 폼 + 항목 + 라이브러리
    cur.execute("""SELECT id, name, description, loop_mode, is_default, status, created_at, updated_at
                   FROM ds_playlists WHERE id=%s""", (pid,))
    r = cur.fetchone()
    if not r:
        conn.close()
        flash("존재하지 않는 플레이리스트.", "danger")
        return redirect(url_for("signage.playlists"))
    playlist = {
        'id': r[0], 'name': r[1], 'description': r[2], 'loop_mode': r[3],
        'is_default': r[4], 'status': r[5], 'created_at': r[6], 'updated_at': r[7]
    }

    # 현재 항목들
    cur.execute("""SELECT pi.id, pi.content_id, pi.seq, pi.duration_sec, pi.weight,
                          c.title, c.type, c.duration_sec AS c_duration,
                          c.file_path, c.body_text, c.web_url
                   FROM ds_playlist_items pi
                   JOIN ds_contents c ON pi.content_id=c.id
                   WHERE pi.playlist_id=%s ORDER BY pi.seq""", (pid,))
    items = [
        {'item_id': r[0], 'content_id': r[1], 'seq': r[2],
         'duration_sec': r[3], 'weight': r[4],
         'title': r[5], 'type': r[6], 'c_duration': r[7],
         'file_path': r[8], 'body_text': r[9], 'web_url': r[10]}
        for r in cur.fetchall()
    ]

    # 라이브러리 (active 콘텐츠 중 아직 추가 안 된 것 우선 표시 + 검색은 클라이언트에서)
    cur.execute("""SELECT id, title, type, duration_sec, file_path, body_text, web_url, status
                   FROM ds_contents WHERE status='active'
                   ORDER BY updated_at DESC LIMIT 500""")
    library = [
        {'id': r[0], 'title': r[1], 'type': r[2], 'duration_sec': r[3],
         'file_path': r[4], 'body_text': r[5], 'web_url': r[6], 'status': r[7]}
        for r in cur.fetchall()
    ]

    conn.close()
    return render_template("signage/playlist_form.html",
                           mode='edit', playlist=playlist, items=items, library=library,
                           is_admin=session.get('role') == 'admin')


@signage_bp.route("/api/playlists/<int:pid>/items", methods=["POST"])
@_login_required
def playlist_items_save(pid):
    """플레이리스트 항목 일괄 저장 (드래그앤드롭 순서 + duration override)"""
    if not _has_signage_perm("update") and session.get("role") != "admin":
        return jsonify({"ok": False, "msg": "권한 없음"}), 403

    data = request.get_json(silent=True) or {}
    items = data.get('items', [])  # [{content_id, duration_sec, weight}, ...]

    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM ds_playlist_items WHERE playlist_id=%s", (pid,))
        for seq, it in enumerate(items, start=1):
            cur.execute("""INSERT INTO ds_playlist_items
                (playlist_id, content_id, seq, duration_sec, weight)
                VALUES (%s, %s, %s, %s, %s)""",
                (pid, int(it['content_id']), seq,
                 int(it.get('duration_sec')) if it.get('duration_sec') else None,
                 int(it.get('weight', 1))))
        conn.commit()
        return jsonify({"ok": True, "count": len(items)})
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "msg": str(e)}), 500
    finally:
        conn.close()


@signage_bp.route("/playlists/<int:pid>/delete", methods=["POST"])
@_login_required
def playlist_delete(pid):
    """플레이리스트 삭제"""
    if not _has_signage_perm("delete") and session.get("role") != "admin":
        return jsonify({"ok": False, "msg": "권한 없음"}), 403

    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM ds_playlist_items WHERE playlist_id=%s", (pid,))
        cur.execute("DELETE FROM ds_playlists WHERE id=%s", (pid,))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "msg": str(e)}), 500
    finally:
        conn.close()


# ════════════════════════════════════════════════════════
#  Phase 3 — 편성표 (시간대/요일별 자동 전환)
# ════════════════════════════════════════════════════════

WEEKDAY_KO = ['월', '화', '수', '목', '금', '토', '일']


def _parse_weekdays(s):
    """'0,1,2,3,4' → [0,1,2,3,4] (월=0 ... 일=6)"""
    if not s:
        return []
    out = []
    for x in str(s).split(','):
        x = x.strip()
        if x.isdigit() and 0 <= int(x) <= 6:
            out.append(int(x))
    return sorted(set(out))


def _get_active_schedule_for(display_id, cur):
    """현재 시각/요일에 활성화된 편성을 priority DESC 순으로 1건 반환"""
    cur.execute("""SELECT s.id, s.layout_id, s.playlist_id, s.content_id,
                          s.start_date, s.end_date, s.start_time, s.end_time,
                          s.recurrence, s.weekdays, s.exclude_holidays,
                          s.target_type, s.target_id, s.priority
                   FROM ds_schedules s
                   WHERE s.status='active'
                     AND (s.start_date IS NULL OR s.start_date <= CURDATE())
                     AND (s.end_date IS NULL OR s.end_date >= CURDATE())
                     AND (s.start_time IS NULL OR s.start_time <= CURTIME())
                     AND (s.end_time IS NULL OR s.end_time >= CURTIME())
                   ORDER BY s.priority DESC, s.id DESC""")
    rows = cur.fetchall()
    if not rows:
        return None

    # 디스플레이의 그룹 멤버십
    cur.execute("SELECT group_id FROM ds_display_group_map WHERE display_id=%s", (display_id,))
    disp_groups = {r[0] for r in cur.fetchall()}
    # primary group (legacy)
    cur.execute("SELECT group_id FROM ds_displays WHERE id=%s", (display_id,))
    rr = cur.fetchone()
    if rr and rr[0]:
        disp_groups.add(rr[0])

    today_weekday = datetime.now().weekday()   # 0=Mon
    today_str = datetime.now().strftime('%Y-%m-%d')

    # 공휴일 체크 (있으면)
    is_holiday = False
    try:
        import holidays as _holidays
        is_holiday = today_str in _holidays.KR(years=datetime.now().year)
    except Exception:
        pass

    for r in rows:
        (sid, layout_id, playlist_id, content_id,
         sd, ed, st, et, recurrence, weekdays, exclude_holidays,
         target_type, target_id, prio) = r

        # 대상 매칭
        if target_type == 'display':
            if target_id != display_id:
                continue
        elif target_type == 'group':
            if target_id not in disp_groups:
                continue
        # 'all'은 무조건 통과

        # 요일 필터 (weekly 또는 daily 둘 다 적용 가능)
        wkd = _parse_weekdays(weekdays)
        if wkd and today_weekday not in wkd:
            continue

        # 공휴일 제외 옵션
        if exclude_holidays and is_holiday:
            continue

        # once: 시작일에만
        if recurrence == 'once' and sd and sd.strftime('%Y-%m-%d') != today_str:
            continue

        return {
            'id': sid, 'layout_id': layout_id, 'playlist_id': playlist_id,
            'content_id': content_id, 'priority': prio
        }

    return None


@signage_bp.route("/schedules")
@_login_required
def schedules():
    """편성표 목록"""
    if not _has_signage_perm("view") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("dashboard"))

    conn = _conn(); cur = conn.cursor()
    cur.execute("""SELECT s.id, s.name, s.target_type, s.target_id,
                          s.layout_id, s.playlist_id, s.content_id,
                          s.start_date, s.end_date, s.start_time, s.end_time,
                          s.recurrence, s.weekdays, s.exclude_holidays,
                          s.priority, s.status, s.created_at,
                          p.name AS pname, l.name AS lname, c.title AS ctitle,
                          d.name AS dname, g.name AS gname
                   FROM ds_schedules s
                   LEFT JOIN ds_playlists p ON s.playlist_id=p.id
                   LEFT JOIN ds_layouts l ON s.layout_id=l.id
                   LEFT JOIN ds_contents c ON s.content_id=c.id
                   LEFT JOIN ds_displays d ON (s.target_type='display' AND s.target_id=d.id)
                   LEFT JOIN ds_display_groups g ON (s.target_type='group' AND s.target_id=g.id)
                   ORDER BY s.status='active' DESC, s.priority DESC, s.id DESC""")
    items = []
    for r in cur.fetchall():
        items.append({
            'id': r[0], 'name': r[1], 'target_type': r[2], 'target_id': r[3],
            'layout_id': r[4], 'playlist_id': r[5], 'content_id': r[6],
            'start_date': r[7], 'end_date': r[8],
            'start_time': r[9], 'end_time': r[10],
            'recurrence': r[11], 'weekdays': _parse_weekdays(r[12]),
            'exclude_holidays': bool(r[13]),
            'priority': r[14], 'status': r[15], 'created_at': r[16],
            'playlist_name': r[17], 'layout_name': r[18], 'content_title': r[19],
            'display_name': r[20], 'group_name': r[21]
        })
    conn.close()
    return render_template("signage/schedules.html",
                           schedules=items,
                           weekday_ko=WEEKDAY_KO,
                           is_admin=session.get('role') == 'admin',
                           can_edit=_has_signage_perm("create") or session.get('role') == 'admin')


def _schedule_form_data(cur):
    """폼 dropdown 데이터"""
    cur.execute("SELECT id, name, is_default FROM ds_playlists WHERE status='active' ORDER BY is_default DESC, name")
    playlists = [{'id': r[0], 'name': r[1], 'is_default': r[2]} for r in cur.fetchall()]
    cur.execute("SELECT id, name FROM ds_layouts ORDER BY is_system DESC, name")
    layouts = [{'id': r[0], 'name': r[1]} for r in cur.fetchall()]
    cur.execute("SELECT id, title FROM ds_contents WHERE status='active' ORDER BY title LIMIT 200")
    contents = [{'id': r[0], 'title': r[1]} for r in cur.fetchall()]
    cur.execute("SELECT id, name, location FROM ds_displays WHERE paired=1 ORDER BY name")
    displays = [{'id': r[0], 'name': r[1], 'location': r[2]} for r in cur.fetchall()]
    cur.execute("SELECT id, name FROM ds_display_groups ORDER BY name")
    groups = [{'id': r[0], 'name': r[1]} for r in cur.fetchall()]
    return playlists, layouts, contents, displays, groups


@signage_bp.route("/schedules/new", methods=["GET", "POST"])
@_login_required
def schedule_new():
    if not _has_signage_perm("create") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("signage.schedules"))

    conn = _conn(); cur = conn.cursor()

    if request.method == "POST":
        try:
            name = request.form.get('name', '').strip()
            target_type = request.form.get('target_type', 'all')
            target_id = request.form.get('target_id') or None
            layout_id = request.form.get('layout_id') or None
            playlist_id = request.form.get('playlist_id') or None
            content_id = request.form.get('content_id') or None
            start_date = request.form.get('start_date') or None
            end_date = request.form.get('end_date') or None
            start_time = request.form.get('start_time') or None
            end_time = request.form.get('end_time') or None
            recurrence = request.form.get('recurrence', 'daily')
            weekdays = ','.join(request.form.getlist('weekdays'))
            exclude_holidays = 1 if request.form.get('exclude_holidays') else 0
            priority = int(request.form.get('priority', 0) or 0)
            status = request.form.get('status', 'active')

            if not name:
                flash("이름을 입력하세요.", "danger")
                conn.close()
                return redirect(url_for("signage.schedule_new"))

            cur.execute("""INSERT INTO ds_schedules
                (name, target_type, target_id, layout_id, playlist_id, content_id,
                 start_date, end_date, start_time, end_time,
                 recurrence, weekdays, exclude_holidays, priority, status, created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (name, target_type,
                 int(target_id) if target_id and target_type != 'all' else None,
                 int(layout_id) if layout_id else None,
                 int(playlist_id) if playlist_id else None,
                 int(content_id) if content_id else None,
                 start_date, end_date, start_time, end_time,
                 recurrence, weekdays, exclude_holidays, priority, status,
                 session.get('user_id')))
            conn.commit()
            flash(f"편성표 '{name}' 등록 완료.", "success")
            conn.close()
            return redirect(url_for("signage.schedules"))
        except Exception as e:
            conn.rollback()
            flash(f"등록 실패: {e}", "danger")

    playlists, layouts, contents, displays, groups = _schedule_form_data(cur)
    conn.close()
    return render_template("signage/schedule_form.html",
                           mode='new', schedule=None,
                           playlists=playlists, layouts=layouts, contents=contents,
                           displays=displays, groups=groups,
                           weekday_ko=WEEKDAY_KO,
                           is_admin=session.get('role') == 'admin')


@signage_bp.route("/schedules/<int:sid>/edit", methods=["GET", "POST"])
@_login_required
def schedule_edit(sid):
    if not _has_signage_perm("update") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("signage.schedules"))

    conn = _conn(); cur = conn.cursor()

    if request.method == "POST":
        try:
            name = request.form.get('name', '').strip()
            target_type = request.form.get('target_type', 'all')
            target_id = request.form.get('target_id') or None
            layout_id = request.form.get('layout_id') or None
            playlist_id = request.form.get('playlist_id') or None
            content_id = request.form.get('content_id') or None
            start_date = request.form.get('start_date') or None
            end_date = request.form.get('end_date') or None
            start_time = request.form.get('start_time') or None
            end_time = request.form.get('end_time') or None
            recurrence = request.form.get('recurrence', 'daily')
            weekdays = ','.join(request.form.getlist('weekdays'))
            exclude_holidays = 1 if request.form.get('exclude_holidays') else 0
            priority = int(request.form.get('priority', 0) or 0)
            status = request.form.get('status', 'active')

            cur.execute("""UPDATE ds_schedules SET
                name=%s, target_type=%s, target_id=%s,
                layout_id=%s, playlist_id=%s, content_id=%s,
                start_date=%s, end_date=%s, start_time=%s, end_time=%s,
                recurrence=%s, weekdays=%s, exclude_holidays=%s,
                priority=%s, status=%s
                WHERE id=%s""",
                (name, target_type,
                 int(target_id) if target_id and target_type != 'all' else None,
                 int(layout_id) if layout_id else None,
                 int(playlist_id) if playlist_id else None,
                 int(content_id) if content_id else None,
                 start_date, end_date, start_time, end_time,
                 recurrence, weekdays, exclude_holidays, priority, status, sid))
            conn.commit()
            flash("저장됨.", "success")
            conn.close()
            return redirect(url_for("signage.schedules"))
        except Exception as e:
            conn.rollback()
            flash(f"저장 실패: {e}", "danger")

    cur.execute("""SELECT id, name, target_type, target_id, layout_id, playlist_id, content_id,
                          start_date, end_date, start_time, end_time,
                          recurrence, weekdays, exclude_holidays, priority, status
                   FROM ds_schedules WHERE id=%s""", (sid,))
    r = cur.fetchone()
    if not r:
        conn.close()
        flash("존재하지 않는 편성.", "danger")
        return redirect(url_for("signage.schedules"))
    schedule = {
        'id': r[0], 'name': r[1], 'target_type': r[2], 'target_id': r[3],
        'layout_id': r[4], 'playlist_id': r[5], 'content_id': r[6],
        'start_date': r[7], 'end_date': r[8], 'start_time': r[9], 'end_time': r[10],
        'recurrence': r[11], 'weekdays': _parse_weekdays(r[12]),
        'exclude_holidays': bool(r[13]),
        'priority': r[14], 'status': r[15]
    }
    playlists, layouts, contents, displays, groups = _schedule_form_data(cur)
    conn.close()
    return render_template("signage/schedule_form.html",
                           mode='edit', schedule=schedule,
                           playlists=playlists, layouts=layouts, contents=contents,
                           displays=displays, groups=groups,
                           weekday_ko=WEEKDAY_KO,
                           is_admin=session.get('role') == 'admin')


@signage_bp.route("/schedules/<int:sid>/delete", methods=["POST"])
@_login_required
def schedule_delete(sid):
    if not _has_signage_perm("delete") and session.get("role") != "admin":
        return jsonify({"ok": False, "msg": "권한 없음"}), 403
    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM ds_schedules WHERE id=%s", (sid,))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "msg": str(e)}), 500
    finally:
        conn.close()


@signage_bp.route("/schedules/<int:sid>/toggle", methods=["POST"])
@_login_required
def schedule_toggle(sid):
    """active <-> paused 토글"""
    if not _has_signage_perm("update") and session.get("role") != "admin":
        return jsonify({"ok": False, "msg": "권한 없음"}), 403
    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("""UPDATE ds_schedules
                       SET status=CASE WHEN status='active' THEN 'paused' ELSE 'active' END
                       WHERE id=%s""", (sid,))
        conn.commit()
        cur.execute("SELECT status FROM ds_schedules WHERE id=%s", (sid,))
        st = cur.fetchone()
        return jsonify({"ok": True, "status": st[0] if st else None})
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "msg": str(e)}), 500
    finally:
        conn.close()


# ════════════════════════════════════════════════════════
#  Phase 4 — 멀티 레이아웃 (split2/3/overlay …)
# ════════════════════════════════════════════════════════

@signage_bp.route("/layouts")
@_login_required
def layouts():
    """레이아웃 목록"""
    if not _has_signage_perm("view") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("dashboard"))

    conn = _conn(); cur = conn.cursor()
    cur.execute("""SELECT l.id, l.name, l.template_type, l.resolution, l.is_system,
                          l.created_at,
                          (SELECT COUNT(*) FROM ds_layout_zones WHERE layout_id=l.id) AS zone_cnt
                   FROM ds_layouts l
                   ORDER BY l.is_system DESC, l.name""")
    items = [
        {'id': r[0], 'name': r[1], 'template_type': r[2], 'resolution': r[3],
         'is_system': r[4], 'created_at': r[5], 'zone_cnt': r[6] or 0}
        for r in cur.fetchall()
    ]
    conn.close()
    return render_template("signage/layouts.html",
                           layouts=items,
                           is_admin=session.get('role') == 'admin',
                           can_edit=_has_signage_perm("create") or session.get('role') == 'admin')


@signage_bp.route("/layouts/<int:lid>")
@_login_required
def layout_detail(lid):
    """레이아웃 상세 — 영역 시각화"""
    if not _has_signage_perm("view") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("dashboard"))

    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT id, name, template_type, resolution, is_system FROM ds_layouts WHERE id=%s", (lid,))
    r = cur.fetchone()
    if not r:
        conn.close()
        flash("존재하지 않는 레이아웃.", "danger")
        return redirect(url_for("signage.layouts"))
    layout = {
        'id': r[0], 'name': r[1], 'template_type': r[2],
        'resolution': r[3], 'is_system': r[4]
    }
    # 해상도 파싱 (3840x2160 → 3840, 2160)
    try:
        rw, rh = layout['resolution'].split('x')
        layout['res_w'] = int(rw); layout['res_h'] = int(rh)
    except Exception:
        layout['res_w'] = 3840; layout['res_h'] = 2160

    cur.execute("""SELECT id, zone_key, x, y, w, h, z_index FROM ds_layout_zones
                   WHERE layout_id=%s ORDER BY z_index, id""", (lid,))
    zones = [
        {'id': r[0], 'zone_key': r[1], 'x': r[2], 'y': r[3],
         'w': r[4], 'h': r[5], 'z_index': r[6]}
        for r in cur.fetchall()
    ]
    conn.close()
    return render_template("signage/layout_detail.html",
                           layout=layout, zones=zones,
                           is_admin=session.get('role') == 'admin')


@signage_bp.route("/layouts/new", methods=["GET", "POST"])
@_login_required
def layout_new():
    if not _has_signage_perm("create") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("signage.layouts"))

    conn = _conn(); cur = conn.cursor()

    if request.method == "POST":
        try:
            name = request.form.get('name', '').strip()
            template_type = request.form.get('template_type', 'full')
            resolution = request.form.get('resolution', '3840x2160').strip()
            zones_json = request.form.get('zones_json', '[]')

            if not name:
                flash("이름을 입력하세요.", "danger")
                conn.close()
                return redirect(url_for("signage.layout_new"))

            cur.execute("""INSERT INTO ds_layouts (name, template_type, resolution, is_system)
                           VALUES (%s,%s,%s,0)""",
                        (name, template_type, resolution))
            lid = cur.lastrowid

            try:
                zones = json.loads(zones_json)
            except Exception:
                zones = []
            for z in zones:
                cur.execute("""INSERT INTO ds_layout_zones
                    (layout_id, zone_key, x, y, w, h, z_index)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (lid, str(z.get('zone_key', 'main'))[:20],
                     int(z.get('x', 0)), int(z.get('y', 0)),
                     int(z.get('w', 100)), int(z.get('h', 100)),
                     int(z.get('z_index', 0))))
            conn.commit()
            flash(f"레이아웃 '{name}' 생성 완료.", "success")
            conn.close()
            return redirect(url_for("signage.layout_detail", lid=lid))
        except Exception as e:
            conn.rollback()
            flash(f"생성 실패: {e}", "danger")

    conn.close()
    return render_template("signage/layout_form.html",
                           mode='new', layout=None, zones=[],
                           is_admin=session.get('role') == 'admin')


@signage_bp.route("/layouts/<int:lid>/edit", methods=["GET", "POST"])
@_login_required
def layout_edit(lid):
    if not _has_signage_perm("update") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("signage.layouts"))

    conn = _conn(); cur = conn.cursor()

    # 시스템 레이아웃 보호
    cur.execute("SELECT id, name, template_type, resolution, is_system FROM ds_layouts WHERE id=%s", (lid,))
    r = cur.fetchone()
    if not r:
        conn.close()
        flash("존재하지 않는 레이아웃.", "danger")
        return redirect(url_for("signage.layouts"))
    if r[4]:
        conn.close()
        flash("시스템 기본 레이아웃은 수정할 수 없습니다.", "danger")
        return redirect(url_for("signage.layout_detail", lid=lid))

    if request.method == "POST":
        try:
            name = request.form.get('name', '').strip()
            template_type = request.form.get('template_type', 'full')
            resolution = request.form.get('resolution', '3840x2160').strip()
            zones_json = request.form.get('zones_json', '[]')

            cur.execute("""UPDATE ds_layouts SET name=%s, template_type=%s, resolution=%s
                           WHERE id=%s AND is_system=0""",
                        (name, template_type, resolution, lid))
            # 영역 전체 재구성
            cur.execute("DELETE FROM ds_layout_zones WHERE layout_id=%s", (lid,))
            try:
                zones = json.loads(zones_json)
            except Exception:
                zones = []
            for z in zones:
                cur.execute("""INSERT INTO ds_layout_zones
                    (layout_id, zone_key, x, y, w, h, z_index)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (lid, str(z.get('zone_key', 'main'))[:20],
                     int(z.get('x', 0)), int(z.get('y', 0)),
                     int(z.get('w', 100)), int(z.get('h', 100)),
                     int(z.get('z_index', 0))))
            conn.commit()
            flash("저장됨.", "success")
            conn.close()
            return redirect(url_for("signage.layout_detail", lid=lid))
        except Exception as e:
            conn.rollback()
            flash(f"저장 실패: {e}", "danger")

    layout = {
        'id': r[0], 'name': r[1], 'template_type': r[2],
        'resolution': r[3], 'is_system': r[4]
    }
    cur.execute("""SELECT id, zone_key, x, y, w, h, z_index FROM ds_layout_zones
                   WHERE layout_id=%s ORDER BY z_index, id""", (lid,))
    zones = [
        {'id': r[0], 'zone_key': r[1], 'x': r[2], 'y': r[3],
         'w': r[4], 'h': r[5], 'z_index': r[6]}
        for r in cur.fetchall()
    ]
    conn.close()
    return render_template("signage/layout_form.html",
                           mode='edit', layout=layout, zones=zones,
                           is_admin=session.get('role') == 'admin')


@signage_bp.route("/api/layouts/<int:lid>/zones")
@_login_required
def api_layout_zones(lid):
    """레이아웃 영역 JSON (썸네일용)"""
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT resolution FROM ds_layouts WHERE id=%s", (lid,))
    r = cur.fetchone()
    if not r:
        conn.close()
        return jsonify({"ok": False, "msg": "없음"}), 404
    try:
        rw, rh = r[0].split('x'); rw = int(rw); rh = int(rh)
    except Exception:
        rw, rh = 3840, 2160
    cur.execute("""SELECT zone_key, x, y, w, h, z_index FROM ds_layout_zones
                   WHERE layout_id=%s ORDER BY z_index, id""", (lid,))
    zones = [{'zone_key': r[0], 'x': r[1], 'y': r[2], 'w': r[3], 'h': r[4], 'z_index': r[5]}
             for r in cur.fetchall()]
    conn.close()
    return jsonify({"ok": True, "res_w": rw, "res_h": rh, "zones": zones})


@signage_bp.route("/layouts/<int:lid>/delete", methods=["POST"])
@_login_required
def layout_delete(lid):
    if not _has_signage_perm("delete") and session.get("role") != "admin":
        return jsonify({"ok": False, "msg": "권한 없음"}), 403
    conn = _conn(); cur = conn.cursor()
    try:
        # 시스템 레이아웃 보호
        cur.execute("SELECT is_system FROM ds_layouts WHERE id=%s", (lid,))
        r = cur.fetchone()
        if not r:
            return jsonify({"ok": False, "msg": "없음"}), 404
        if r[0]:
            return jsonify({"ok": False, "msg": "시스템 기본 레이아웃은 삭제 불가"}), 400
        cur.execute("DELETE FROM ds_layout_zones WHERE layout_id=%s", (lid,))
        cur.execute("DELETE FROM ds_layouts WHERE id=%s AND is_system=0", (lid,))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "msg": str(e)}), 500
    finally:
        conn.close()


# ════════════════════════════════════════════════════════
#  Phase 1.3 — 디스플레이 + 송출 페이지
# ════════════════════════════════════════════════════════

import random, string

PAIR_CODE_LEN = 6
PAIR_EXPIRE_MIN = 10


def _gen_pair_code(cur):
    """중복 안 되는 6자리 영숫자 코드 생성"""
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=PAIR_CODE_LEN))
        cur.execute("SELECT 1 FROM ds_displays WHERE pair_code=%s AND paired=0", (code,))
        if not cur.fetchone():
            return code


def _gen_token():
    return secrets.token_urlsafe(32)


def _mark_offline_displays(cur, threshold_sec=120):
    """heartbeat 끊긴 지 threshold_sec 이상 되면 offline 처리"""
    cur.execute("""UPDATE ds_displays
                   SET status='offline'
                   WHERE status='online'
                     AND last_seen IS NOT NULL
                     AND TIMESTAMPDIFF(SECOND, last_seen, NOW()) > %s""",
                (threshold_sec,))


@signage_bp.route("/displays")
@_login_required
def displays():
    """디스플레이 목록"""
    if not _has_signage_perm("view") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("dashboard"))

    conn = _conn(); cur = conn.cursor()
    _mark_offline_displays(cur)
    conn.commit()

    cur.execute("""SELECT d.id, d.name, d.location, d.group_id, d.default_playlist_id,
                          d.resolution, d.orientation, d.token, d.paired,
                          d.last_seen, d.current_content_id, d.status,
                          d.ip_address, d.created_at,
                          p.name AS playlist_name,
                          c.title AS current_content_title
                   FROM ds_displays d
                   LEFT JOIN ds_playlists p ON d.default_playlist_id = p.id
                   LEFT JOIN ds_contents c ON d.current_content_id = c.id
                   WHERE d.paired=1
                   ORDER BY (d.status='online') DESC, d.name""")
    displays_list = [
        {'id': r[0], 'name': r[1], 'location': r[2], 'group_id': r[3],
         'default_playlist_id': r[4], 'resolution': r[5], 'orientation': r[6],
         'token': r[7], 'paired': r[8], 'last_seen': r[9],
         'current_content_id': r[10], 'status': r[11],
         'ip_address': r[12], 'created_at': r[13],
         'playlist_name': r[14], 'current_content_title': r[15]}
        for r in cur.fetchall()
    ]
    conn.close()
    return render_template("signage/displays.html",
                           displays=displays_list,
                           is_admin=session.get('role') == 'admin',
                           can_edit=_has_signage_perm("create") or session.get('role') == 'admin')


@signage_bp.route("/displays/new", methods=["GET", "POST"])
@_login_required
def display_new():
    """관리자가 페어링 코드 입력 → 디스플레이 등록 완료"""
    if not _has_signage_perm("create") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("signage.displays"))

    conn = _conn(); cur = conn.cursor()

    if request.method == "POST":
        code = request.form.get('pair_code', '').strip().upper()
        name = request.form.get('name', '').strip()
        location = request.form.get('location', '').strip()
        playlist_id = request.form.get('default_playlist_id') or None
        orientation = request.form.get('orientation', 'landscape')

        if not code or not name:
            flash("페어링 코드와 이름을 입력하세요.", "danger")
            conn.close()
            return redirect(url_for("signage.display_new"))

        # 코드로 임시 row 찾기
        cur.execute("""SELECT id FROM ds_displays
                       WHERE pair_code=%s AND paired=0
                         AND (pair_expires_at IS NULL OR pair_expires_at > NOW())""",
                    (code,))
        row = cur.fetchone()
        if not row:
            flash("페어링 코드가 유효하지 않거나 만료됐습니다. TV에서 코드를 다시 받아주세요.", "danger")
            conn.close()
            return redirect(url_for("signage.display_new"))

        did = row[0]
        try:
            cur.execute("""UPDATE ds_displays SET
                name=%s, location=%s, default_playlist_id=%s, orientation=%s,
                paired=1, pair_code=NULL, pair_expires_at=NULL
                WHERE id=%s""",
                (name, location, playlist_id if playlist_id else None,
                 orientation, did))
            cur.execute("""INSERT INTO ds_display_logs (display_id, event_type, message)
                           VALUES (%s, 'paired', %s)""",
                        (did, f"Paired by user_id={session.get('user_id')}"))
            conn.commit()
            flash(f"디스플레이 '{name}' 등록 완료. TV가 자동으로 재생을 시작합니다.", "success")
            conn.close()
            return redirect(url_for("signage.displays"))
        except Exception as e:
            conn.rollback()
            flash(f"등록 실패: {e}", "danger")

    # GET — 폼
    cur.execute("SELECT id, name, is_default FROM ds_playlists WHERE status='active' ORDER BY is_default DESC, name")
    playlists = [{'id': r[0], 'name': r[1], 'is_default': r[2]} for r in cur.fetchall()]
    conn.close()
    return render_template("signage/display_form.html",
                           mode='new', display=None, playlists=playlists,
                           is_admin=session.get('role') == 'admin')


@signage_bp.route("/displays/<int:did>/edit", methods=["GET", "POST"])
@_login_required
def display_edit(did):
    """디스플레이 정보 수정"""
    if not _has_signage_perm("update") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("signage.displays"))

    conn = _conn(); cur = conn.cursor()

    if request.method == "POST":
        name = request.form.get('name', '').strip()
        location = request.form.get('location', '').strip()
        playlist_id = request.form.get('default_playlist_id') or None
        orientation = request.form.get('orientation', 'landscape')

        try:
            cur.execute("""UPDATE ds_displays SET
                name=%s, location=%s, default_playlist_id=%s, orientation=%s
                WHERE id=%s""",
                (name, location, playlist_id if playlist_id else None, orientation, did))
            conn.commit()
            flash("저장됨.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"저장 실패: {e}", "danger")
        conn.close()
        return redirect(url_for("signage.displays"))

    cur.execute("""SELECT id, name, location, group_id, default_playlist_id,
                          resolution, orientation, token, paired, status
                   FROM ds_displays WHERE id=%s AND paired=1""", (did,))
    r = cur.fetchone()
    if not r:
        conn.close()
        flash("디스플레이 없음.", "danger")
        return redirect(url_for("signage.displays"))
    display = {
        'id': r[0], 'name': r[1], 'location': r[2], 'group_id': r[3],
        'default_playlist_id': r[4], 'resolution': r[5], 'orientation': r[6],
        'token': r[7], 'paired': r[8], 'status': r[9]
    }
    cur.execute("SELECT id, name, is_default FROM ds_playlists WHERE status='active' ORDER BY is_default DESC, name")
    playlists = [{'id': r[0], 'name': r[1], 'is_default': r[2]} for r in cur.fetchall()]
    conn.close()
    return render_template("signage/display_form.html",
                           mode='edit', display=display, playlists=playlists,
                           is_admin=session.get('role') == 'admin')


@signage_bp.route("/displays/<int:did>/delete", methods=["POST"])
@_login_required
def display_delete(did):
    if not _has_signage_perm("delete") and session.get("role") != "admin":
        return jsonify({"ok": False, "msg": "권한 없음"}), 403
    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM ds_displays WHERE id=%s", (did,))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "msg": str(e)}), 500
    finally:
        conn.close()


# ── 송출 페이지 (인증 불필요 — token 기반) ───────────────

@signage_bp.route("/play/pair")
def play_pair():
    """TV가 처음 진입 → 6자리 코드 발급, 등록 대기"""
    conn = _conn(); cur = conn.cursor()
    code = _gen_pair_code(cur)
    token = _gen_token()
    cur.execute("""INSERT INTO ds_displays
        (name, pair_code, pair_expires_at, token, paired,
         status, ip_address, user_agent)
        VALUES ('', %s, NOW() + INTERVAL %s MINUTE, %s, 0,
                'offline', %s, %s)""",
        (code, PAIR_EXPIRE_MIN, token,
         request.remote_addr,
         (request.headers.get('User-Agent') or '')[:200]))
    did = cur.lastrowid
    conn.commit()
    conn.close()
    return render_template("signage/play_pair.html",
                           pair_code=code, token=token, display_id=did,
                           expire_min=PAIR_EXPIRE_MIN)


@signage_bp.route("/api/play/pair_status/<token>")
def play_pair_status(token):
    """페어링 완료 여부 폴링"""
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT paired, name FROM ds_displays WHERE token=%s", (token,))
    r = cur.fetchone()
    conn.close()
    if not r:
        return jsonify({"paired": False, "expired": True})
    return jsonify({"paired": bool(r[0]), "name": r[1] or ""})


@signage_bp.route("/play/<token>")
def play(token):
    """송출 페이지 — TV 풀스크린 진입점"""
    conn = _conn(); cur = conn.cursor()
    cur.execute("""SELECT id, name, paired, status FROM ds_displays WHERE token=%s""",
                (token,))
    r = cur.fetchone()
    if not r:
        conn.close()
        return "Invalid token", 404
    if not r[2]:  # not paired
        conn.close()
        return redirect(url_for("signage.play_pair"))

    did = r[0]
    cur.execute("""UPDATE ds_displays SET
        status='online', last_seen=NOW(), ip_address=%s, user_agent=%s
        WHERE id=%s""",
        (request.remote_addr, (request.headers.get('User-Agent') or '')[:200], did))
    cur.execute("""INSERT INTO ds_display_logs (display_id, event_type, message)
                   VALUES (%s, 'online', 'Player started')""", (did,))
    conn.commit()
    conn.close()
    return render_template("signage/play.html",
                           token=token, display_id=did, display_name=r[1])


def _get_active_playlist_for(display_id, cur):
    """디스플레이에 송출할 플레이리스트 결정"""
    # 1. 디스플레이 지정 플레이리스트
    cur.execute("""SELECT p.id FROM ds_displays d
                   JOIN ds_playlists p ON d.default_playlist_id=p.id
                   WHERE d.id=%s AND p.status='active'""", (display_id,))
    r = cur.fetchone()
    if r:
        return r[0]
    # 2. is_default 플레이리스트
    cur.execute("SELECT id FROM ds_playlists WHERE is_default=1 AND status='active' LIMIT 1")
    r = cur.fetchone()
    return r[0] if r else None


def _get_playlist_items(playlist_id, cur):
    """플레이리스트 항목 + 콘텐츠 정보"""
    cur.execute("""SELECT pi.content_id, IFNULL(pi.duration_sec, c.duration_sec),
                          c.title, c.type, c.body_text, c.file_path, c.web_url, c.priority
                   FROM ds_playlist_items pi
                   JOIN ds_contents c ON pi.content_id=c.id
                   WHERE pi.playlist_id=%s
                     AND c.status='active'
                     AND (c.start_at IS NULL OR c.start_at <= NOW())
                     AND (c.end_at IS NULL OR c.end_at >= NOW())
                   ORDER BY pi.seq""",
                (playlist_id,))
    return [
        {'content_id': r[0], 'duration_sec': r[1],
         'title': r[2], 'type': r[3], 'body_text': r[4],
         'file_path': r[5], 'web_url': r[6], 'priority': r[7]}
        for r in cur.fetchall()
    ]


@signage_bp.route("/api/play/<token>/poll")
def play_poll(token):
    """현재 재생할 콘텐츠 목록 반환"""
    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT id, paired, status FROM ds_displays WHERE token=%s", (token,))
    r = cur.fetchone()
    if not r:
        conn.close()
        return jsonify({"ok": False, "msg": "invalid token"}), 404
    if not r[1]:
        conn.close()
        return jsonify({"ok": False, "msg": "not paired"}), 403

    did = r[0]
    # heartbeat 갱신
    cur.execute("UPDATE ds_displays SET last_seen=NOW(), status='online' WHERE id=%s", (did,))

    # 긴급공지 우선
    cur.execute("""SELECT id, title, body, image_path, bg_color
                   FROM ds_emergency_messages
                   WHERE status='active'
                     AND (start_at IS NULL OR start_at <= NOW())
                     AND (end_at IS NULL OR end_at >= NOW())
                   ORDER BY created_at DESC LIMIT 1""")
    em = cur.fetchone()
    if em:
        conn.commit(); conn.close()
        return jsonify({
            "ok": True, "emergency": True,
            "emergency_id": em[0], "title": em[1], "body": em[2],
            "image_path": em[3], "bg_color": em[4] or '#dc2626',
            "items": [], "playlist_id": None, "loop_mode": "sequential"
        })

    # 편성표 우선 적용 (시간대/요일/그룹 일치하면 schedule이 결정)
    sched = _get_active_schedule_for(did, cur)
    schedule_id = None
    layout_payload = None
    pid = None

    if sched:
        schedule_id = sched['id']
        # 편성표가 단일 콘텐츠를 직접 지정한 경우 → 임시 단일 항목 플레이리스트
        if sched['content_id'] and not sched['playlist_id']:
            cur.execute("""SELECT id, title, type, body_text, file_path, web_url,
                                  duration_sec, priority
                           FROM ds_contents WHERE id=%s AND status='active'""",
                        (sched['content_id'],))
            c = cur.fetchone()
            if c:
                items = [{'content_id': c[0], 'title': c[1], 'type': c[2],
                          'body_text': c[3], 'file_path': c[4], 'web_url': c[5],
                          'duration_sec': c[6], 'priority': c[7]}]
                # 레이아웃 동봉
                if sched['layout_id']:
                    layout_payload = _build_layout_payload(sched['layout_id'], cur)
                conn.commit(); conn.close()
                return jsonify({"ok": True, "items": items,
                                "playlist_id": None, "schedule_id": schedule_id,
                                "loop_mode": "sequential",
                                "layout": layout_payload, "emergency": False})
        # 편성표 플레이리스트
        if sched['playlist_id']:
            pid = sched['playlist_id']
        # 레이아웃
        if sched['layout_id']:
            layout_payload = _build_layout_payload(sched['layout_id'], cur)

    # 편성표가 없거나 playlist가 비면 → 디스플레이 기본 플레이리스트
    if not pid:
        pid = _get_active_playlist_for(did, cur)

    if not pid:
        conn.commit(); conn.close()
        return jsonify({"ok": True, "items": [], "playlist_id": None,
                        "schedule_id": schedule_id, "layout": layout_payload,
                        "msg": "no playlist", "loop_mode": "sequential"})

    items = _get_playlist_items(pid, cur)
    cur.execute("SELECT loop_mode FROM ds_playlists WHERE id=%s", (pid,))
    lm_row = cur.fetchone()
    loop_mode = lm_row[0] if lm_row else 'sequential'

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "items": items, "playlist_id": pid,
                    "schedule_id": schedule_id,
                    "loop_mode": loop_mode, "layout": layout_payload,
                    "emergency": False})


def _build_layout_payload(layout_id, cur):
    """레이아웃 + 영역 정보를 송출용으로 직렬화"""
    cur.execute("""SELECT id, name, template_type, resolution
                   FROM ds_layouts WHERE id=%s""", (layout_id,))
    r = cur.fetchone()
    if not r:
        return None
    res = r[3] or '3840x2160'
    try:
        rw, rh = res.split('x'); rw = int(rw); rh = int(rh)
    except Exception:
        rw, rh = 3840, 2160
    cur.execute("""SELECT zone_key, x, y, w, h, z_index
                   FROM ds_layout_zones WHERE layout_id=%s
                   ORDER BY z_index, id""", (layout_id,))
    zones = []
    for zr in cur.fetchall():
        zones.append({
            'zone_key': zr[0],
            'x_pct': round(zr[1] * 100.0 / rw, 3) if rw else 0,
            'y_pct': round(zr[2] * 100.0 / rh, 3) if rh else 0,
            'w_pct': round(zr[3] * 100.0 / rw, 3) if rw else 100,
            'h_pct': round(zr[4] * 100.0 / rh, 3) if rh else 100,
            'z_index': zr[5]
        })
    return {
        'id': r[0], 'name': r[1], 'template_type': r[2],
        'resolution': res, 'zones': zones
    }


@signage_bp.route("/api/play/<token>/heartbeat", methods=["POST"])
def play_heartbeat(token):
    """heartbeat — 디스플레이 online 유지"""
    conn = _conn(); cur = conn.cursor()
    cur.execute("""UPDATE ds_displays SET last_seen=NOW(), status='online'
                   WHERE token=%s AND paired=1""", (token,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "ts": datetime.now().isoformat()})


@signage_bp.route("/api/play/<token>/log", methods=["POST"])
def play_log(token):
    """재생로그 기록 — TV가 콘텐츠 표시 시작/종료 시 호출"""
    data = request.get_json(silent=True) or {}
    content_id = data.get('content_id')
    playlist_id = data.get('playlist_id')
    duration_sec = data.get('duration_sec')

    conn = _conn(); cur = conn.cursor()
    cur.execute("SELECT id FROM ds_displays WHERE token=%s AND paired=1", (token,))
    r = cur.fetchone()
    if not r:
        conn.close()
        return jsonify({"ok": False}), 404
    did = r[0]

    cur.execute("""INSERT INTO ds_play_logs
        (display_id, content_id, playlist_id, duration_sec)
        VALUES (%s, %s, %s, %s)""",
        (did, content_id, playlist_id, duration_sec))
    if content_id:
        cur.execute("UPDATE ds_displays SET current_content_id=%s WHERE id=%s",
                    (content_id, did))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════
#  Phase 5 — 긴급공지
# ════════════════════════════════════════════════════════

@signage_bp.route("/emergency")
@_login_required
def emergency():
    """긴급공지 목록 + 발송 폼"""
    if not _has_signage_perm("view") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("dashboard"))

    conn = _conn(); cur = conn.cursor()
    # 만료된 활성 공지 자동 종료
    cur.execute("""UPDATE ds_emergency_messages
                   SET status='ended', ended_at=NOW()
                   WHERE status='active' AND end_at IS NOT NULL AND end_at < NOW()""")
    conn.commit()

    # 활성
    cur.execute("""SELECT id, title, body, image_path, target_type, target_ids,
                          bg_color, start_at, end_at, status, created_by,
                          created_at, ended_at,
                          (SELECT name FROM tuser WHERE id=ds_emergency_messages.created_by) AS sender
                   FROM ds_emergency_messages
                   WHERE status='active'
                   ORDER BY created_at DESC""")
    active = _emergency_rows(cur.fetchall())

    # 최근 이력 (종료된 것 30건)
    cur.execute("""SELECT id, title, body, image_path, target_type, target_ids,
                          bg_color, start_at, end_at, status, created_by,
                          created_at, ended_at,
                          (SELECT name FROM tuser WHERE id=ds_emergency_messages.created_by) AS sender
                   FROM ds_emergency_messages
                   WHERE status='ended'
                   ORDER BY ended_at DESC LIMIT 30""")
    history = _emergency_rows(cur.fetchall())

    # 디스플레이/그룹 목록 (대상 선택용)
    cur.execute("""SELECT id, name, location FROM ds_displays
                   WHERE paired=1 ORDER BY name""")
    displays = [{'id': r[0], 'name': r[1], 'location': r[2]} for r in cur.fetchall()]
    cur.execute("SELECT id, name FROM ds_display_groups ORDER BY name")
    groups = [{'id': r[0], 'name': r[1]} for r in cur.fetchall()]

    conn.close()
    return render_template("signage/emergency.html",
                           active=active, history=history,
                           displays=displays, groups=groups,
                           is_admin=session.get('role') == 'admin',
                           can_publish=_has_signage_perm("publish") or session.get('role') == 'admin')


def _emergency_rows(rows):
    items = []
    for r in rows:
        try:
            target_ids = json.loads(r[5]) if r[5] else []
        except Exception:
            target_ids = []
        items.append({
            'id': r[0], 'title': r[1], 'body': r[2], 'image_path': r[3],
            'target_type': r[4], 'target_ids': target_ids,
            'bg_color': r[6], 'start_at': r[7], 'end_at': r[8],
            'status': r[9], 'created_by': r[10],
            'created_at': r[11], 'ended_at': r[12],
            'sender': r[13]
        })
    return items


@signage_bp.route("/emergency/send", methods=["POST"])
@_login_required
def emergency_send():
    """긴급공지 발송"""
    if not _has_signage_perm("publish") and session.get("role") != "admin":
        flash("긴급공지 발송 권한이 없습니다.", "danger")
        return redirect(url_for("signage.emergency"))

    title = request.form.get('title', '').strip()
    body = request.form.get('body', '').strip()
    target_type = request.form.get('target_type', 'all')
    target_ids_raw = request.form.getlist('target_ids')
    bg_color = request.form.get('bg_color', '#dc2626')
    duration_min = request.form.get('duration_min', '').strip()

    if not title:
        flash("제목을 입력하세요.", "danger")
        return redirect(url_for("signage.emergency"))

    # 이미지 업로드 (선택)
    image_path = None
    if 'image' in request.files and request.files['image'].filename:
        try:
            image_path = _save_upload(request.files['image'], 'image')
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("signage.emergency"))

    target_ids = []
    if target_type in ('group', 'display') and target_ids_raw:
        target_ids = [int(x) for x in target_ids_raw if x.isdigit()]

    end_at = None
    if duration_min and duration_min.isdigit() and int(duration_min) > 0:
        from datetime import timedelta
        end_at = datetime.now() + timedelta(minutes=int(duration_min))

    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("""INSERT INTO ds_emergency_messages
            (title, body, image_path, target_type, target_ids, bg_color,
             start_at, end_at, status, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s, 'active', %s)""",
            (title, body, image_path, target_type,
             json.dumps(target_ids) if target_ids else None,
             bg_color, end_at, session.get('user_id')))
        conn.commit()
        flash(f"🚨 긴급공지 '{title}' 발송 완료. TV 다음 폴링(최대 15초)에 즉시 표시됩니다.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"발송 실패: {e}", "danger")
    finally:
        conn.close()
    return redirect(url_for("signage.emergency"))


@signage_bp.route("/emergency/<int:eid>/end", methods=["POST"])
@_login_required
def emergency_end(eid):
    """긴급공지 종료"""
    if not _has_signage_perm("publish") and session.get("role") != "admin":
        return jsonify({"ok": False, "msg": "권한 없음"}), 403

    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("""UPDATE ds_emergency_messages
            SET status='ended', ended_at=NOW(), ended_by=%s
            WHERE id=%s AND status='active'""",
            (session.get('user_id'), eid))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "msg": str(e)}), 500
    finally:
        conn.close()


# ════════════════════════════════════════════════════════
#  Phase 6 — 재생로그 / 증빙
# ════════════════════════════════════════════════════════

@signage_bp.route("/logs")
@_login_required
def logs():
    """재생로그 + 통계 + 장애이력"""
    if not _has_signage_perm("view") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("dashboard"))

    from datetime import timedelta
    # 필터
    date_from = request.args.get('from', '').strip()
    date_to = request.args.get('to', '').strip()
    f_display = request.args.get('display_id', '').strip()
    f_content = request.args.get('content_id', '').strip()
    tab = request.args.get('tab', 'recent')   # recent / content / display / event

    if not date_from:
        date_from = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    if not date_to:
        date_to = datetime.now().strftime('%Y-%m-%d')

    conn = _conn(); cur = conn.cursor()

    where = ["pl.started_at >= %s", "pl.started_at < DATE_ADD(%s, INTERVAL 1 DAY)"]
    params = [date_from, date_to]
    if f_display:
        where.append("pl.display_id = %s"); params.append(f_display)
    if f_content:
        where.append("pl.content_id = %s"); params.append(f_content)
    where_sql = " AND ".join(where)

    # 최근 로그 200건
    cur.execute(f"""SELECT pl.id, pl.started_at, pl.ended_at, pl.duration_sec,
                          d.name AS dname, d.location,
                          c.title AS ctitle, c.type AS ctype,
                          p.name AS pname
                   FROM ds_play_logs pl
                   LEFT JOIN ds_displays d ON pl.display_id = d.id
                   LEFT JOIN ds_contents c ON pl.content_id = c.id
                   LEFT JOIN ds_playlists p ON pl.playlist_id = p.id
                   WHERE {where_sql}
                   ORDER BY pl.started_at DESC LIMIT 200""", params)
    recent_logs = [
        {'id': r[0], 'started_at': r[1], 'ended_at': r[2], 'duration_sec': r[3],
         'display_name': r[4], 'location': r[5],
         'content_title': r[6], 'content_type': r[7],
         'playlist_name': r[8]}
        for r in cur.fetchall()
    ]

    # 콘텐츠별 통계
    cur.execute(f"""SELECT c.id, c.title, c.type,
                          COUNT(*) AS play_count,
                          SUM(IFNULL(pl.duration_sec,0)) AS total_sec
                   FROM ds_play_logs pl
                   JOIN ds_contents c ON pl.content_id = c.id
                   WHERE {where_sql}
                   GROUP BY c.id, c.title, c.type
                   ORDER BY play_count DESC LIMIT 50""", params)
    content_stats = [
        {'id': r[0], 'title': r[1], 'type': r[2],
         'play_count': r[3], 'total_sec': int(r[4] or 0)}
        for r in cur.fetchall()
    ]

    # 디스플레이별 통계
    cur.execute(f"""SELECT d.id, d.name, d.location, d.status,
                          COUNT(*) AS play_count,
                          SUM(IFNULL(pl.duration_sec,0)) AS total_sec,
                          MAX(pl.started_at) AS last_play
                   FROM ds_play_logs pl
                   JOIN ds_displays d ON pl.display_id = d.id
                   WHERE {where_sql}
                   GROUP BY d.id, d.name, d.location, d.status
                   ORDER BY play_count DESC""", params)
    display_stats = [
        {'id': r[0], 'name': r[1], 'location': r[2], 'status': r[3],
         'play_count': r[4], 'total_sec': int(r[5] or 0), 'last_play': r[6]}
        for r in cur.fetchall()
    ]

    # 장애/이벤트 로그
    cur.execute(f"""SELECT dl.id, dl.created_at, dl.event_type, dl.message,
                          d.name AS dname
                   FROM ds_display_logs dl
                   LEFT JOIN ds_displays d ON dl.display_id = d.id
                   WHERE dl.created_at >= %s AND dl.created_at < DATE_ADD(%s, INTERVAL 1 DAY)
                   ORDER BY dl.created_at DESC LIMIT 100""", [date_from, date_to])
    event_logs = [
        {'id': r[0], 'time': r[1], 'type': r[2], 'message': r[3], 'display': r[4]}
        for r in cur.fetchall()
    ]

    # 디스플레이/콘텐츠 목록 (필터 dropdown)
    cur.execute("SELECT id, name FROM ds_displays WHERE paired=1 ORDER BY name")
    all_displays = [{'id': r[0], 'name': r[1]} for r in cur.fetchall()]
    cur.execute("SELECT id, title FROM ds_contents ORDER BY title LIMIT 500")
    all_contents = [{'id': r[0], 'title': r[1]} for r in cur.fetchall()]

    # 총합 카드
    cur.execute(f"""SELECT COUNT(*) AS total_plays,
                           COUNT(DISTINCT pl.display_id) AS active_disp,
                           COUNT(DISTINCT pl.content_id) AS uniq_content,
                           SUM(IFNULL(pl.duration_sec,0)) AS total_sec
                    FROM ds_play_logs pl
                    WHERE {where_sql}""", params)
    s = cur.fetchone()
    summary = {
        'total_plays': s[0] or 0,
        'active_disp': s[1] or 0,
        'uniq_content': s[2] or 0,
        'total_sec': int(s[3] or 0)
    }

    conn.close()
    return render_template("signage/logs.html",
                           recent_logs=recent_logs,
                           content_stats=content_stats,
                           display_stats=display_stats,
                           event_logs=event_logs,
                           summary=summary,
                           all_displays=all_displays, all_contents=all_contents,
                           date_from=date_from, date_to=date_to,
                           filter_display=f_display, filter_content=f_content,
                           tab=tab,
                           is_admin=session.get('role') == 'admin')


@signage_bp.route("/logs/export")
@_login_required
def logs_export():
    """재생로그 CSV 다운로드"""
    if not _has_signage_perm("view") and session.get("role") != "admin":
        flash("권한 없음.", "danger")
        return redirect(url_for("signage.logs"))

    from datetime import timedelta
    import csv, io as _io
    from flask import Response

    date_from = request.args.get('from', '').strip()
    date_to = request.args.get('to', '').strip()
    f_display = request.args.get('display_id', '').strip()
    f_content = request.args.get('content_id', '').strip()
    if not date_from:
        date_from = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    if not date_to:
        date_to = datetime.now().strftime('%Y-%m-%d')

    where = ["pl.started_at >= %s", "pl.started_at < DATE_ADD(%s, INTERVAL 1 DAY)"]
    params = [date_from, date_to]
    if f_display:
        where.append("pl.display_id = %s"); params.append(f_display)
    if f_content:
        where.append("pl.content_id = %s"); params.append(f_content)
    where_sql = " AND ".join(where)

    conn = _conn(); cur = conn.cursor()
    cur.execute(f"""SELECT pl.started_at, pl.ended_at, pl.duration_sec,
                          d.name, d.location,
                          c.title, c.type, p.name
                   FROM ds_play_logs pl
                   LEFT JOIN ds_displays d ON pl.display_id = d.id
                   LEFT JOIN ds_contents c ON pl.content_id = c.id
                   LEFT JOIN ds_playlists p ON pl.playlist_id = p.id
                   WHERE {where_sql}
                   ORDER BY pl.started_at DESC LIMIT 50000""", params)
    rows = cur.fetchall()
    conn.close()

    out = _io.StringIO()
    out.write('﻿')  # UTF-8 BOM (Excel 한글)
    w = csv.writer(out)
    w.writerow(['시작시각', '종료시각', '재생시간(초)', '디스플레이', '위치', '콘텐츠', '유형', '플레이리스트'])
    for r in rows:
        w.writerow([
            r[0].strftime('%Y-%m-%d %H:%M:%S') if r[0] else '',
            r[1].strftime('%Y-%m-%d %H:%M:%S') if r[1] else '',
            r[2] or 0,
            r[3] or '', r[4] or '', r[5] or '', r[6] or '', r[7] or ''
        ])

    fname = f"signage_logs_{date_from}_{date_to}.csv"
    return Response(out.getvalue().encode('utf-8'),
                    mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename={fname}'})


@signage_bp.route("/settings")
@_admin_required
def settings():
    flash("설정 — Phase 7에서 구현 예정", "info")
    return redirect(url_for("signage.dashboard"))
