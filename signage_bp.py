"""㈜ 태인 — 디지털 사이니지 관리 (Phase 0)
Flask Blueprint. url_prefix=/signage, template_folder=signage.
세션 키는 메인 앱과 통일 (user_id, role, e_id, user_name) — SSO.

Phase 0 범위:
- DB 스키마 15개 테이블 생성
- 메뉴 진입점 (/signage 대시보드)
- 권한 체크 헬퍼

Phase 1 (MVP)에서 추가 예정:
- 콘텐츠 CRUD
- 플레이리스트 CRUD
- 디스플레이 페어링
- 송출 페이지 /signage/play/<token>
"""
import os
import secrets
from datetime import datetime
from functools import wraps

import pymysql
from dotenv import load_dotenv
from flask import (Blueprint, render_template, request, flash, jsonify,
                   redirect, url_for, session)

load_dotenv()

signage_bp = Blueprint("signage", __name__, url_prefix="/signage",
                       template_folder="signage")


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
            name VARCHAR(100) NOT NULL,
            location VARCHAR(200),
            group_id INT NULL,
            resolution VARCHAR(20) DEFAULT '3840x2160',
            orientation ENUM('landscape','portrait') NOT NULL DEFAULT 'landscape',
            token VARCHAR(64) UNIQUE,
            pair_code VARCHAR(8) NULL,
            pair_expires_at DATETIME NULL,
            last_seen DATETIME NULL,
            current_content_id INT NULL,
            status ENUM('online','offline','paused','error') NOT NULL DEFAULT 'offline',
            ip_address VARCHAR(50),
            user_agent VARCHAR(200),
            meta JSON NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_status (status),
            INDEX idx_group (group_id),
            INDEX idx_token (token)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

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


# ── Phase 1+에서 추가될 라우트 자리표시자 ───────────────
@signage_bp.route("/contents")
@_login_required
def contents():
    flash("콘텐츠 관리 — Phase 1에서 구현 예정", "info")
    return redirect(url_for("signage.dashboard"))


@signage_bp.route("/playlists")
@_login_required
def playlists():
    flash("플레이리스트 — Phase 1에서 구현 예정", "info")
    return redirect(url_for("signage.dashboard"))


@signage_bp.route("/schedules")
@_login_required
def schedules():
    flash("편성표 — Phase 3에서 구현 예정", "info")
    return redirect(url_for("signage.dashboard"))


@signage_bp.route("/layouts")
@_login_required
def layouts():
    flash("레이아웃 — Phase 4에서 구현 예정", "info")
    return redirect(url_for("signage.dashboard"))


@signage_bp.route("/displays")
@_login_required
def displays():
    flash("디스플레이 — Phase 1에서 구현 예정", "info")
    return redirect(url_for("signage.dashboard"))


@signage_bp.route("/emergency")
@_login_required
def emergency():
    flash("긴급공지 — Phase 5에서 구현 예정", "info")
    return redirect(url_for("signage.dashboard"))


@signage_bp.route("/logs")
@_login_required
def logs():
    flash("재생로그 — Phase 6에서 구현 예정", "info")
    return redirect(url_for("signage.dashboard"))


@signage_bp.route("/settings")
@_admin_required
def settings():
    flash("설정 — Phase 7에서 구현 예정", "info")
    return redirect(url_for("signage.dashboard"))
