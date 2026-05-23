import paramiko, time, base64, sys, tarfile, io, os, subprocess
from dotenv import load_dotenv

load_dotenv()

REBUILD = "--rebuild" in sys.argv  # python deploy_and_restart.py --rebuild
print(f"📦 배포 시작 — 예상 소요시간: {'약 3분 (이미지 재빌드)' if REBUILD else '약 15초'}")
print("=" * 45)

# ── 0) git 자동 커밋 + 이력 JSON 생성 ─────────────────────
def _git_auto_commit():
    import json as _json
    try:
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        if status:
            changed = [l[3:] for l in status.splitlines()][:8]
            summary = ", ".join(changed) + (" 외 다수" if len(changed) >= 8 else "")
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
    # 위험물·안전관리
    ("hazmat_bp.py", "hazmat_bp.py"),
    ("templates/hazmat.html", "templates/hazmat.html"),
    ("templates/hazmat_recipients.html", "templates/hazmat_recipients.html"),
    ("templates/hazmat_alerts.html", "templates/hazmat_alerts.html"),
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

# ── 1) tar.gz 생성 ──────────────────────────────────────────────
print("[████░░░░░░░░░░░░░░░░]  20% tar.gz 생성중...")
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode='w:gz') as tar:
    for local, remote in files:
        if os.path.exists(local):
            tar.add(local, arcname=remote)
        else:
            print(f"  ⚠️  {local} 없음 — 건너뜀")
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

nas(f"mkdir -p {STAGE_DIR}/static {STAGE_DIR}/templates {STAGE_DIR}/templates/tbm {STAGE_DIR}/uploads/documents")

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
    (f"{STAGE_DIR}/tbm_bp.py",           f"{DOCKER_APP_DIR}/tbm_bp.py"),
    (f"{STAGE_DIR}/kepco_collector.py", f"{DOCKER_APP_DIR}/kepco_collector.py"),
    (f"{STAGE_DIR}/kepco_analyzer.py",  f"{DOCKER_APP_DIR}/kepco_analyzer.py"),
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
docker_files_tbm = [
    (f"{STAGE_DIR}/tbm_app.py",    f"{DOCKER_APP_DIR}/tbm_app.py"),
    (f"{STAGE_DIR}/.env",          f"{DOCKER_APP_DIR}/.env"),
]
for local, remote in files:
    if local.startswith("templates/tbm"):
        docker_files_tbm.append((f"{STAGE_DIR}/{local}", f"{DOCKER_APP_DIR}/{remote}"))

cp_ok = True
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
