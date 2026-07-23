import paramiko, time, base64, sys, tarfile, io, os, subprocess
from dotenv import load_dotenv

load_dotenv()

REBUILD = "--rebuild" in sys.argv  # python deploy_and_restart.py --rebuild
print(f"📦 배포 시작 — 예상 소요시간: {'약 3분 (이미지 재빌드)' if REBUILD else '약 15초'}")
print("=" * 45)

# ── 0) git 자동 커밋 + 이력 JSON 생성 ─────────────────────
# 파일 → 한글 화면명 (배포 이력을 사람이 읽을 수 있게)
_FILE_LABELS = {
    "app_maria.py": "메인 서버",
    "templates/work_report.html": "근무보고서",
    "templates/schedule_record.html": "근무표기록관리",
    "templates/roster.html": "명부관리",
    "templates/attendance.html": "근태현황",
    "templates/dashboard.html": "대시보드",
    "templates/user_management.html": "사용자관리",
    "templates/leave_approval.html": "휴가결재",
    "templates/leave_plan_view.html": "연차계획",
    "templates/annual_leave.html": "연차관리",
    "templates/work_schedule.html": "근무표",
    "templates/meal_management.html": "식수관리",
    "templates/document_management.html": "문서관리",
    "templates/education.html": "교육관리",
    "templates/hazmat.html": "위험물관리",
    "templates/mes_realtime.html": "MES 실시간",
    "templates/power_dashboard.html": "전력관리",
    "templates/fire_management.html": "화재감시",
    "erp_sync.py": "ERP 동기화",
    "erp_enter_sync.py": "ERP 출입연동",
    "mes_bp.py": "MES",
    "edu_bp.py": "교육관리",
    "hazmat_bp.py": "위험물관리",
    "tbm_bp.py": "TBM",
    "signage_bp.py": "사이니지",
    "deploy_and_restart.py": "배포스크립트",
}
# 이력 제목에서 제외할 시스템/자동생성 파일
_SKIP_PREFIXES = (".bkit/", "static/dev_history.json", "_archive/", "nohup.out")


# 화면명 → 이력 카테고리 라벨
_CATEGORY = {
    "근무보고서": "근무보고서", "근무표기록관리": "근무표", "근무표": "근무표",
    "명부관리": "명부", "근태현황": "근태", "대시보드": "대시보드",
    "사용자관리": "사용자", "휴가결재": "휴가", "연차계획": "휴가", "연차관리": "휴가",
    "식수관리": "식수", "문서관리": "문서", "교육관리": "교육", "위험물관리": "위험물",
    "MES": "MES", "MES 실시간": "MES", "전력관리": "전력", "화재감시": "화재",
    "ERP 동기화": "ERP", "TBM": "TBM", "사이니지": "사이니지",
    "메인 서버": "시스템", "배포스크립트": "시스템",
}


def _auto_summary():
    """변경 파일을 [라벨] 한글요약 형태로 정리 (시스템 파일 제외)"""
    status = subprocess.run(["git", "status", "--porcelain"],
                            capture_output=True, text=True).stdout.strip()
    if not status:
        return None, False
    files = [l[3:].strip().strip('"') for l in status.splitlines()]
    meaningful = [f for f in files if not any(f.startswith(p) for p in _SKIP_PREFIXES)]
    if not meaningful:
        return "[시스템] 자동 생성 파일 갱신", True
    labels = []
    for f in meaningful:
        lb = _FILE_LABELS.get(f) or os.path.basename(f).rsplit(".", 1)[0]
        if lb not in labels:
            labels.append(lb)
    # 대표 화면으로 카테고리 결정 (시스템 아닌 것 우선)
    cat = next((_CATEGORY[l] for l in labels
                if l in _CATEGORY and _CATEGORY[l] != "시스템"), None)
    if not cat:
        cat = _CATEGORY.get(labels[0], "시스템")
    body = ", ".join(labels[:5]) + (" 외" if len(labels) > 5 else "")
    return f"[{cat}] {body} 수정", True


def _git_auto_commit():
    import json as _json
    try:
        # --msg "내용" 으로 직접 설명 지정 가능 (권장)
        msg_arg = None
        if "--msg" in sys.argv:
            i = sys.argv.index("--msg")
            if i + 1 < len(sys.argv):
                msg_arg = sys.argv[i + 1]
        summary, has_change = _auto_summary()
        if has_change:
            if msg_arg:
                # --msg에 [라벨]이 없으면 자동 판정한 카테고리를 붙임
                if msg_arg.startswith("["):
                    summary = msg_arg
                else:
                    auto_cat = summary[1:summary.index("]")] if summary.startswith("[") else "시스템"
                    summary = f"[{auto_cat}] {msg_arg}"
            subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"배포: {summary}"], check=True, capture_output=True)
            print(f"  📝 git 커밋: {summary}")
    except Exception as e:
        print(f"  ⚠️  git 커밋 실패 (무시): {e}")
    # 최신 git 커밋을 dev_history.json 맨 앞에 추가 (기존 이력 보존)
    try:
        hist_path = "static/dev_history.json"
        existing = []
        if os.path.exists(hist_path):
            with open(hist_path, encoding="utf-8") as f:
                existing = _json.load(f)
        existing_shas = {e["sha"] for e in existing}
        log = subprocess.run(
            ["git", "log", "--pretty=format:%H|%ad|%s", "--date=format:%Y-%m-%d %H:%M", "--max-count=5"],
            capture_output=True, text=True).stdout.strip()
        new_entries = []
        for line in log.splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                sha = parts[0][:7]
                if sha not in existing_shas:
                    new_entries.append({"sha": sha, "date": parts[1], "msg": parts[2]})
        if new_entries:
            combined = new_entries + existing
            with open(hist_path, "w", encoding="utf-8") as f:
                _json.dump(combined, f, ensure_ascii=False)
    except Exception as e:
        print(f"  ⚠️  이력 JSON 업데이트 실패 (무시): {e}")

_git_auto_commit()

files = [
    ("Dockerfile", "Dockerfile"),
    ("requirements.txt", "requirements.txt"),
    ("app_maria.py", "app_maria.py"),
    ("tbm_bp.py", "tbm_bp.py"),
    ("static/style.css", "static/style.css"),
    ("static/LOGO.GIF", "static/LOGO.GIF"),
    ("static/manifest.json", "static/manifest.json"),
    ("templates/attendance.html", "templates/attendance.html"),
    ("templates/weekly52.html", "templates/weekly52.html"),
    ("templates/work_schedule.html", "templates/work_schedule.html"),
    ("templates/annual_leave.html", "templates/annual_leave.html"),
    ("templates/login.html", "templates/login.html"),
    ("templates/change_password.html", "templates/change_password.html"),
    ("templates/log_management.html", "templates/log_management.html"),
    ("templates/meal_management.html", "templates/meal_management.html"),
    ("templates/meal_mobile.html", "templates/meal_mobile.html"),
    ("templates/user_management.html", "templates/user_management.html"),
    ("templates/perm_groups.html", "templates/perm_groups.html"),
    ("templates/survey_list.html", "templates/survey_list.html"),
    ("templates/survey_my.html", "templates/survey_my.html"),
    ("templates/survey_edit.html", "templates/survey_edit.html"),
    ("templates/survey_preview.html", "templates/survey_preview.html"),
    ("templates/survey_stats.html", "templates/survey_stats.html"),
    ("templates/dev_history.html", "templates/dev_history.html"),
    ("static/dev_history.json", "static/dev_history.json"),
    ("templates/_survey_q_render.html", "templates/_survey_q_render.html"),
    ("templates/roster.html", "templates/roster.html"),
    ("templates/schedule_record.html", "templates/schedule_record.html"),
    ("templates/dashboard.html", "templates/dashboard.html"),
    ("templates/document_management.html", "templates/document_management.html"),
    ("templates/_sidebar.html", "templates/_sidebar.html"),
    ("templates/leave_approval.html", "templates/leave_approval.html"),
    ("templates/leave_plan_view.html", "templates/leave_plan_view.html"),
    ("templates/work_report.html", "templates/work_report.html"),
    (".env", ".env"),
    ("templates/fire_management.html", "templates/fire_management.html"),
    # 교육/훈련 관리
    ("edu_bp.py", "edu_bp.py"),
    ("templates/education.html", "templates/education.html"),
    ("templates/education_courses.html", "templates/education_courses.html"),
    ("templates/education_sessions.html", "templates/education_sessions.html"),
    ("templates/education_detail.html", "templates/education_detail.html"),
    ("tuya_fire.py", "tuya_fire.py"),
    ("erp_sync.py", "erp_sync.py"),
    ("erp_enter_sync.py", "erp_enter_sync.py"),
    ("backfill_tenter.py", "backfill_tenter.py"),
    ("fix_tuser_id.py", "fix_tuser_id.py"),
    ("merge_tuser.py", "merge_tuser.py"),
    ("add_park.py", "add_park.py"),
    ("fill_cards.py", "fill_cards.py"),
    ("erp_inspect.py", "erp_inspect.py"),
    # 위험물·안전관리
    ("hazmat_bp.py", "hazmat_bp.py"),
    ("templates/hazmat.html", "templates/hazmat.html"),
    ("templates/hazmat_recipients.html", "templates/hazmat_recipients.html"),
    ("templates/hazmat_alerts.html", "templates/hazmat_alerts.html"),
    # AI 안전진단
    ("safety_ai_bp.py", "safety_ai_bp.py"),
    ("templates/safety_ai_inspect.html", "templates/safety_ai_inspect.html"),
    ("templates/safety_ai_report.html",  "templates/safety_ai_report.html"),
    ("templates/safety_ai_edit.html",    "templates/safety_ai_edit.html"),
    ("templates/safety_ai_stats.html",   "templates/safety_ai_stats.html"),
    ("templates/welfare.html", "templates/welfare.html"),
    # MES 관리
    ("mes_bp.py", "mes_bp.py"),
    ("templates/mes_realtime.html", "templates/mes_realtime.html"),
    ("templates/mes_report.html",   "templates/mes_report.html"),
    ("templates/mes_devices.html",  "templates/mes_devices.html"),
    ("templates/pacemaker_dashboard.html", "templates/pacemaker_dashboard.html"),
    ("templates/esg_placeholder.html", "templates/esg_placeholder.html"),
    ("templates/env_dashboard.html", "templates/env_dashboard.html"),
    ("templates/mes_env_dashboard.html", "templates/mes_env_dashboard.html"),
    ("templates/it_request.html", "templates/it_request.html"),
    ("templates/it_manage.html", "templates/it_manage.html"),
    # 버스배차관리
    ("templates/bus_members.html", "templates/bus_members.html"),
    ("templates/bus_dispatch.html", "templates/bus_dispatch.html"),
    ("templates/bus_sms_log.html", "templates/bus_sms_log.html"),
    # KEPCO 전력관리
    ("kepco_collector.py", "kepco_collector.py"),
    ("kepco_analyzer.py", "kepco_analyzer.py"),
    ("templates/power_dashboard.html", "templates/power_dashboard.html"),
    ("templates/power_history.html", "templates/power_history.html"),
    ("templates/power_alerts.html", "templates/power_alerts.html"),
    ("templates/power_settings.html", "templates/power_settings.html"),
    # TBM 관리 시스템
    ("tbm_app.py", "tbm_app.py"),
    ("start_tbm.sh", "start_tbm.sh"),
    ("docker-compose.yml", "docker-compose.yml"),
    # 디지털 사이니지
    ("signage_bp.py", "signage_bp.py"),
    ("templates/signage/dashboard.html", "templates/signage/dashboard.html"),
    ("templates/signage/contents.html", "templates/signage/contents.html"),
    ("templates/signage/content_form.html", "templates/signage/content_form.html"),
    ("templates/signage/content_preview.html", "templates/signage/content_preview.html"),
    ("templates/signage/playlists.html", "templates/signage/playlists.html"),
    ("templates/signage/playlist_form.html", "templates/signage/playlist_form.html"),
    ("templates/signage/displays.html", "templates/signage/displays.html"),
    ("templates/signage/display_form.html", "templates/signage/display_form.html"),
    ("templates/signage/play_pair.html", "templates/signage/play_pair.html"),
    ("templates/signage/play.html", "templates/signage/play.html"),
    ("templates/signage/emergency.html", "templates/signage/emergency.html"),
    ("templates/signage/logs.html", "templates/signage/logs.html"),
    ("templates/signage/schedules.html", "templates/signage/schedules.html"),
    ("templates/signage/schedule_form.html", "templates/signage/schedule_form.html"),
    ("templates/signage/layouts.html", "templates/signage/layouts.html"),
    ("templates/signage/layout_detail.html", "templates/signage/layout_detail.html"),
    ("templates/signage/layout_form.html", "templates/signage/layout_form.html"),
    ("templates/signage/settings.html", "templates/signage/settings.html"),
    ("templates/signage/templates.html", "templates/signage/templates.html"),
    ("templates/signage/birthday_form.html", "templates/signage/birthday_form.html"),
    ("templates/signage/slide_editor.html", "templates/signage/slide_editor.html"),
    ("templates/signage/block_editor.html", "templates/signage/block_editor.html"),
    ("templates/signage/content_writer.html", "templates/signage/content_writer.html"),
    ("templates/tbm/base.html", "templates/tbm/base.html"),
    ("templates/tbm/login.html", "templates/tbm/login.html"),
    ("templates/tbm/dashboard.html", "templates/tbm/dashboard.html"),
    ("templates/tbm/week_detail.html", "templates/tbm/week_detail.html"),
    ("templates/tbm/sign.html", "templates/tbm/sign.html"),
    ("templates/tbm/history.html", "templates/tbm/history.html"),
    ("templates/tbm/print.html", "templates/tbm/print.html"),
    ("templates/tbm/summary.html", "templates/tbm/summary.html"),
    ("templates/tbm/templates_mgmt.html", "templates/tbm/templates_mgmt.html"),
]

NAS_HOST = os.environ["NAS_HOST"]
NAS_USER = os.environ["NAS_USER"]
NAS_PASS = os.environ["NAS_PASS"]
NAS_SUDO = os.environ["NAS_SUDO"]
STAGE_DIR = os.environ.get("NAS_STAGE_DIR", "/volume1/web/attendance")
DOCKER_MAIN = "attendance-app"
DOCKER_TBM  = "attendance-tbm"
DOCKER_APP_DIR = "/app"

# ── 0) BlockNote 에디터 빌드 (signage-editor/) ──────────────────
import subprocess as _sp
_editor_dir = "signage-editor"
_editor_built = "static/signage-editor/index.html"
if os.path.exists(_editor_dir):
    if "--skip-editor-build" not in sys.argv:
        print("[██░░░░░░░░░░░░░░░░░░]  10% signage-editor 빌드중...")
        try:
            r = _sp.run(["npm", "run", "build"], cwd=_editor_dir, capture_output=True, text=True, timeout=180)
            if r.returncode != 0:
                print("  ⚠️  npm build 실패 — 기존 빌드 산출물 사용")
                print("    " + (r.stderr or r.stdout)[-300:])
            else:
                print("  ✅ signage-editor 빌드 완료")
        except FileNotFoundError:
            print("  ⚠️  npm 없음 (Mac에 Node.js 필요) — 기존 빌드 산출물 사용")
        except Exception as e:
            print(f"  ⚠️  빌드 오류: {e}")

# canvas-editor 빌드
_canvas_dir = "canvas-editor"
if os.path.exists(_canvas_dir):
    if "--skip-editor-build" not in sys.argv:
        print("[██░░░░░░░░░░░░░░░░░░]  12% canvas-editor 빌드중...")
        try:
            r = _sp.run(["npm", "run", "build"], cwd=_canvas_dir, capture_output=True, text=True, timeout=180)
            if r.returncode != 0:
                print("  ⚠️  canvas-editor 빌드 실패 — 기존 산출물 사용")
                print("    " + (r.stderr or r.stdout)[-300:])
            else:
                print("  ✅ canvas-editor 빌드 완료")
        except Exception as e:
            print(f"  ⚠️  canvas-editor 빌드 오류: {e}")

# ── 1) tar.gz 생성 ──────────────────────────────────────────────
print("[████░░░░░░░░░░░░░░░░]  20% tar.gz 생성중...")
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode='w:gz') as tar:
    for local, remote in files:
        if os.path.exists(local):
            tar.add(local, arcname=remote)
        else:
            print(f"  ⚠️  {local} 없음 — 건너뜀")
    # static/signage-editor/ 전체 트리 추가
    if os.path.exists("static/signage-editor"):
        for root, dirs, fs in os.walk("static/signage-editor"):
            for f in fs:
                full = os.path.join(root, f)
                tar.add(full, arcname=full)
    # static/canvas-editor/ 전체 트리 추가
    if os.path.exists("static/canvas-editor"):
        for root, dirs, fs in os.walk("static/canvas-editor"):
            for f in fs:
                full = os.path.join(root, f)
                tar.add(full, arcname=full)
tar_b64 = base64.b64encode(buf.getvalue()).decode()
print(f"[████████░░░░░░░░░░░░]  40% tar.gz 완료 ({len(buf.getvalue())//1024}KB)")

# ── 2) SSH 전송 → NAS 스테이징 디렉터리 ─────────────────────────
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
# SSH 키 인증 시도 → 실패 시 비번 인증 fallback
_ssh_key = os.path.expanduser("~/.ssh/id_tams_nas")
try:
    if os.path.exists(_ssh_key):
        c.connect(NAS_HOST, port=22, username=NAS_USER, key_filename=_ssh_key, timeout=15)
    else:
        c.connect(NAS_HOST, port=22, username=NAS_USER, password=NAS_PASS, timeout=15)
except paramiko.AuthenticationException:
    c.connect(NAS_HOST, port=22, username=NAS_USER, password=NAS_PASS, timeout=15)

def nas(cmd):
    _, stdout, _ = c.exec_command(cmd)
    return stdout.read().decode().strip()

def sudo_nas(cmd):
    # .5 Linux: rose90m이 docker 그룹 → sudo 불필요
    if NAS_HOST.endswith(".5"):
        full = f"sh -c 'PATH=/usr/local/bin:$PATH {cmd}' 2>&1"
        _, stdout, _ = c.exec_command(full)
        return stdout.read().decode().strip()
    # .11 NAS: sudo 필요
    full = f"echo '{NAS_SUDO}' | sudo -S sh -c 'PATH=/usr/local/bin:$PATH {cmd}' 2>&1"
    _, stdout, _ = c.exec_command(full)
    out = stdout.read().decode().strip()
    if out.startswith("Password: "):
        out = out[len("Password: "):]
    return out.strip()

nas(f"mkdir -p {STAGE_DIR}/static {STAGE_DIR}/static/signage-editor {STAGE_DIR}/static/canvas-editor {STAGE_DIR}/templates {STAGE_DIR}/templates/tbm {STAGE_DIR}/templates/signage {STAGE_DIR}/uploads/documents {STAGE_DIR}/uploads/signage")

chunks = [tar_b64[i:i+60000] for i in range(0, len(tar_b64), 60000)]
nas(f"> /tmp/_deploy.tar.gz.b64")
time.sleep(0.3)
for chunk in chunks:
    _, stdout, _ = c.exec_command(f"echo '{chunk}' >> /tmp/_deploy.tar.gz.b64")
    stdout.read()

res = nas(f"base64 -d /tmp/_deploy.tar.gz.b64 > /tmp/_deploy.tar.gz && cd {STAGE_DIR} && tar xzf /tmp/_deploy.tar.gz && rm /tmp/_deploy.tar.gz /tmp/_deploy.tar.gz.b64 && echo OK")
print(f"[████████████░░░░░░░░]  60% NAS 전송 {'✅' if 'OK' in res else '❌ '+res}")

# ── 3) Docker 컨테이너에 파일 복사 ───────────────────────────────
print("[████████████████░░░░]  80% Docker 컨테이너 업데이트중...")

# attendance-app: Python 소스 + 개별 파일 복사 (디렉터리 통째 cp는 중첩 생성 버그 있음)
import glob as _glob
docker_files_main = [
    (f"{STAGE_DIR}/app_maria.py",       f"{DOCKER_APP_DIR}/app_maria.py"),
    (f"{STAGE_DIR}/tuya_fire.py",       f"{DOCKER_APP_DIR}/tuya_fire.py"),
    (f"{STAGE_DIR}/mes_bp.py",           f"{DOCKER_APP_DIR}/mes_bp.py"),
    (f"{STAGE_DIR}/edu_bp.py",           f"{DOCKER_APP_DIR}/edu_bp.py"),
    (f"{STAGE_DIR}/hazmat_bp.py",        f"{DOCKER_APP_DIR}/hazmat_bp.py"),
    (f"{STAGE_DIR}/safety_ai_bp.py",     f"{DOCKER_APP_DIR}/safety_ai_bp.py"),
    (f"{STAGE_DIR}/tbm_bp.py",           f"{DOCKER_APP_DIR}/tbm_bp.py"),
    (f"{STAGE_DIR}/signage_bp.py",       f"{DOCKER_APP_DIR}/signage_bp.py"),
    (f"{STAGE_DIR}/kepco_collector.py", f"{DOCKER_APP_DIR}/kepco_collector.py"),
    (f"{STAGE_DIR}/kepco_analyzer.py",  f"{DOCKER_APP_DIR}/kepco_analyzer.py"),
    (f"{STAGE_DIR}/erp_sync.py",        f"{DOCKER_APP_DIR}/erp_sync.py"),
    (f"{STAGE_DIR}/erp_enter_sync.py",  f"{DOCKER_APP_DIR}/erp_enter_sync.py"),
    (f"{STAGE_DIR}/backfill_tenter.py", f"{DOCKER_APP_DIR}/backfill_tenter.py"),
    (f"{STAGE_DIR}/fix_tuser_id.py",    f"{DOCKER_APP_DIR}/fix_tuser_id.py"),
    (f"{STAGE_DIR}/merge_tuser.py",     f"{DOCKER_APP_DIR}/merge_tuser.py"),
    (f"{STAGE_DIR}/add_park.py",        f"{DOCKER_APP_DIR}/add_park.py"),
    (f"{STAGE_DIR}/fill_cards.py",      f"{DOCKER_APP_DIR}/fill_cards.py"),
    (f"{STAGE_DIR}/erp_inspect.py",     f"{DOCKER_APP_DIR}/erp_inspect.py"),
    (f"{STAGE_DIR}/.env",               f"{DOCKER_APP_DIR}/.env"),
]
# templates 개별 파일 (TBM 통합 이후 templates/tbm도 메인 컨테이너에 포함)
for local, remote in files:
    if local.startswith("templates/"):
        docker_files_main.append((
            f"{STAGE_DIR}/{local}",
            f"{DOCKER_APP_DIR}/{remote}"
        ))
# static 개별 파일
for ext in ["*.css", "*.GIF", "*.gif", "*.json", "*.png", "*.js"]:
    for f_path in _glob.glob(f"static/{ext}"):
        docker_files_main.append((
            f"{STAGE_DIR}/{f_path}",
            f"{DOCKER_APP_DIR}/{f_path}"
        ))
# static/signage-editor/ 빌드 산출물 (디렉토리 통째로 docker cp)
if os.path.exists("static/signage-editor"):
    docker_files_main.append((
        f"{STAGE_DIR}/static/signage-editor",
        f"{DOCKER_APP_DIR}/static/"
    ))
if os.path.exists("static/canvas-editor"):
    docker_files_main.append((
        f"{STAGE_DIR}/static/canvas-editor",
        f"{DOCKER_APP_DIR}/static/"
    ))
docker_files_tbm = [
    (f"{STAGE_DIR}/tbm_app.py",    f"{DOCKER_APP_DIR}/tbm_app.py"),
    (f"{STAGE_DIR}/.env",          f"{DOCKER_APP_DIR}/.env"),
]
for local, remote in files:
    if local.startswith("templates/tbm"):
        docker_files_tbm.append((f"{STAGE_DIR}/{local}", f"{DOCKER_APP_DIR}/{remote}"))

cp_ok = True
# 메인 컨테이너 안에 신규 디렉토리 보장 (signage 등)
sudo_nas(f"docker exec {DOCKER_MAIN} mkdir -p {DOCKER_APP_DIR}/templates/signage {DOCKER_APP_DIR}/uploads/signage {DOCKER_APP_DIR}/static/signage-editor {DOCKER_APP_DIR}/static/canvas-editor")
# 옛 hashed assets 제거
sudo_nas(f"docker exec {DOCKER_MAIN} sh -c 'rm -rf {DOCKER_APP_DIR}/static/signage-editor/assets {DOCKER_APP_DIR}/static/canvas-editor/assets'")
for src, dst in docker_files_main:
    r = sudo_nas(f"docker cp {src} {DOCKER_MAIN}:{dst}; echo EXIT_$?")
    if "EXIT_0" not in r:
        print(f"  ⚠️  cp {src} → {DOCKER_MAIN}:{dst}: {r}")
        cp_ok = False

for src, dst in docker_files_tbm:
    r = sudo_nas(f"docker cp {src} {DOCKER_TBM}:{dst}; echo EXIT_$?")
    if "EXIT_0" not in r:
        print(f"  ⚠️  cp {src} → {DOCKER_TBM}:{dst}: {r}")

# ── 4) Docker 재시작 (또는 이미지 재빌드) ────────────────────────
if REBUILD:
    print("[████████████████░░░░]  80% 이미지 재빌드 중 (약 3분)...")
    r_build = sudo_nas(
        f"cd {STAGE_DIR} && docker compose build app tbm && "
        f"docker compose up -d --no-deps --force-recreate app tbm && echo RST_OK"
    )
    r_main = r_build
    r_tbm  = r_build
    time.sleep(15)
else:
    r_main = sudo_nas(f"docker restart {DOCKER_MAIN} && echo RST_OK")
    r_tbm  = sudo_nas(f"docker restart {DOCKER_TBM}  && echo RST_OK")
    time.sleep(8)

# ── 5) 헬스체크 ──────────────────────────────────────────────────
main_code = sudo_nas("curl -s -o /dev/null -w '%{http_code}' http://localhost:5050/")
tbm_code  = sudo_nas("curl -s -o /dev/null -w '%{http_code}' http://localhost:5051/tbm/login")

c.close()

ok = main_code in ("200","302") and tbm_code in ("200","302")
print(f"[████████████████████] 100% {'✅ 배포 완료! (main:'+main_code+' tbm:'+tbm_code+')' if ok else '⚠️ 확인필요 main:'+main_code+' tbm:'+tbm_code}")
