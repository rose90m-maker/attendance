"""
태인관리시스템 전체 백업 스크립트
실행: python3 backup.py
백업 위치: /Users/changkooji/
"""
import os, time, subprocess, base64, paramiko, json, urllib.request, urllib.parse
from dotenv import load_dotenv

load_dotenv()

# ── 설정 ─────────────────────────────────────────────────
NAS_HOST   = os.environ["NAS_HOST"]
NAS_USER   = os.environ["NAS_USER"]
NAS_PASS   = os.environ["NAS_PASS"]
NAS_SUDO   = os.environ["NAS_SUDO"]
DOCKER     = '/usr/local/bin/docker'
MYSQLDUMP  = '/var/packages/MariaDB10/target/usr/local/mariadb10.11/bin/mysqldump'
SOCKET     = '/run/mysqld/mysqld10.sock'
BACKUP_DIR = '/Users/changkooji'
NAS_BACKUP_DIR = '/volume1/backup/attendance'   # NAS 보조 사본 위치
NAS_KEEP_LAST  = 14                              # 각 파일 종류별 최신 N개 유지
TIMESTAMP  = time.strftime('%Y%m%d_%H%M%S')

def sudo_run(c, cmd, timeout=180):
    stdin, stdout, stderr = c.exec_command(f"sudo -S {cmd}", timeout=timeout)
    stdin.write(NAS_SUDO + "\n"); stdin.flush()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return out, err

def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(NAS_HOST, port=22, username=NAS_USER, password=NAS_PASS)
    return c

print("=" * 50)
total_steps = 7
DSM_URL    = f"http://{NAS_HOST}:5000"

# ── 1. 소스코드 백업 (chroma_db 포함) ─────────────────────
print(f"📦 [1/{total_steps}] 소스코드 백업 중 (chroma_db 포함)...")
src_path = os.path.join(BACKUP_DIR, f"attendance_backup_{TIMESTAMP}.tar.gz")
result = subprocess.run([
    "tar", "-czf", src_path,
    "--exclude=attendance/.venv",
    "--exclude=attendance/__pycache__",
    "--exclude=attendance/*.pyc",
    "attendance/"
], cwd='/Users/changkooji', capture_output=True)

if result.returncode == 0:
    sz = os.path.getsize(src_path)
    print(f"  ✅ 소스코드 백업 완료: {src_path}")
    print(f"     크기: {sz // (1024*1024)}MB")
else:
    print(f"  ❌ 소스코드 백업 실패: {result.stderr.decode()}")

# ── 2. DB 백업 (mysqldump) ────────────────────────────────
print()
print(f"📦 [2/{total_steps}] DB 백업 중 (mysqldump)...")
db_path    = os.path.join(BACKUP_DIR, f"attendance_db_FULL_{TIMESTAMP}.sql")
remote_tmp = f"/tmp/attendance_db_{TIMESTAMP}.sql"

c = connect()
cmd = (f"-u mysql {MYSQLDUMP} --socket={SOCKET} "
       f"--single-transaction --routines --triggers "
       f"attendance > {remote_tmp} && echo OK")
out, err = sudo_run(c, cmd)

if 'OK' in out:
    print(f"  mysqldump 성공, 로컬로 전송 중...")
    stdin3, stdout3, _ = c.exec_command(f"sudo -S base64 {remote_tmp}")
    stdin3.write(NAS_SUDO + "\n"); stdin3.flush()
    b64data = stdout3.read()
    sql_data = base64.b64decode(b64data)
    with open(db_path, 'wb') as f:
        f.write(sql_data)
    sudo_run(c, f"rm -f {remote_tmp}")
    sz = os.path.getsize(db_path)
    print(f"  ✅ DB 백업 완료: {db_path}")
    print(f"     크기: {sz // (1024*1024)}MB")
else:
    print(f"  ❌ DB 백업 실패: {err[:200]}")

# ── 3. Docker 컨테이너 설정 백업 ─────────────────────────
print()
print(f"📦 [3/{total_steps}] Docker 컨테이너 설정 백업 중...")
docker_path = os.path.join(BACKUP_DIR, f"attendance_docker_{TIMESTAMP}.json")
docker_info = {}

for container in ["attendance-app", "attendance-tbm"]:
    out, _ = sudo_run(c, f"{DOCKER} inspect {container} 2>/dev/null")
    if out and out.strip().startswith('['):
        docker_info[container] = json.loads(out)

with open(docker_path, 'w', encoding='utf-8') as f:
    json.dump(docker_info, f, ensure_ascii=False, indent=2)
print(f"  ✅ Docker 설정 백업 완료: {docker_path}")

c.close()

# ── 4. DSM 시스템 설정 백업 ──────────────────────────────
print()
print(f"📦 [4/{total_steps}] DSM 시스템 설정 백업 중...")
dsm_path = os.path.join(BACKUP_DIR, f"attendance_dsm_config_{TIMESTAMP}.dss")
try:
    import ssl, http.cookiejar
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    # DSM 로그인
    login_url = (f"{DSM_URL}/webapi/auth.cgi?api=SYNO.API.Auth&version=3&method=login"
                 f"&account={urllib.parse.quote(NAS_USER)}&passwd={urllib.parse.quote(NAS_PASS)}"
                 f"&session=backup&format=sid")
    with urllib.request.urlopen(login_url, context=ctx, timeout=15) as r:
        res = json.loads(r.read())
    if not res.get('success'):
        raise Exception(f"DSM 로그인 실패: {res}")
    sid = res['data']['sid']
    # 설정 파일 다운로드
    cfg_url = (f"{DSM_URL}/webapi/entry.cgi?api=SYNO.Core.System.Config"
               f"&method=download&version=1&_sid={sid}")
    with urllib.request.urlopen(cfg_url, context=ctx, timeout=30) as r:
        data = r.read()
    # 로그아웃
    urllib.request.urlopen(
        f"{DSM_URL}/webapi/auth.cgi?api=SYNO.API.Auth&version=1&method=logout&session=backup&_sid={sid}",
        context=ctx, timeout=10)
    with open(dsm_path, 'wb') as f:
        f.write(data)
    print(f"  ✅ DSM 설정 백업 완료: {dsm_path} ({len(data)//1024}KB)")
except Exception as e:
    dsm_path = None
    print(f"  ❌ DSM 설정 백업 실패: {e}")

# ── 5. Mac crontab 백업 ───────────────────────────────────
print()
print(f"📦 [5/{total_steps}] Mac crontab 백업 중...")
cron_path = os.path.join(BACKUP_DIR, f"attendance_crontab_{TIMESTAMP}.txt")
result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
with open(cron_path, 'w') as f:
    f.write(result.stdout)
print(f"  ✅ crontab 백업 완료: {cron_path}")

# ── 6. Mac LaunchAgents 백업 ──────────────────────────────
print()
print(f"📦 [6/{total_steps}] Mac LaunchAgents 백업 중...")
launch_dir = '/Users/changkooji/Library/LaunchAgents'
launch_path = os.path.join(BACKUP_DIR, f"attendance_launchagents_{TIMESTAMP}.tar.gz")
plists = [f for f in os.listdir(launch_dir) if 'attendance' in f and f.endswith('.plist')]
if plists:
    files = [os.path.join(launch_dir, f) for f in plists]
    result = subprocess.run(["tar", "-czf", launch_path] + files, capture_output=True)
    if result.returncode == 0:
        print(f"  ✅ LaunchAgents 백업 완료: {launch_path} ({len(plists)}개)")
    else:
        print(f"  ❌ LaunchAgents 백업 실패")
else:
    print(f"  ℹ️  attendance 관련 plist 없음 — 건너뜀")

# ── 7. NAS 자동 복사 (외부 사본) ─────────────────────────
print()
print(f"📦 [7/{total_steps}] NAS({NAS_HOST})로 백업 사본 전송 중...")
local_files = [
    src_path,
    db_path,
    docker_path,
    cron_path,
]
if dsm_path and os.path.exists(dsm_path):
    local_files.append(dsm_path)
if 'launch_path' in dir() and os.path.exists(launch_path):
    local_files.append(launch_path)


def ssh_pipe_upload(local_path, remote_path):
    """sshpass + ssh stdin 파이프로 파일 전송.
    Synology의 SFTP/SCP 서브시스템이 막혀 있어도 동작 (cat > 만 사용)."""
    cmd = ["sshpass", "-p", NAS_PASS,
           "ssh", "-o", "StrictHostKeyChecking=no",
           "-o", "UserKnownHostsFile=/dev/null",
           "-o", "LogLevel=ERROR",
           f"{NAS_USER}@{NAS_HOST}",
           f"cat > {remote_path}"]
    with open(local_path, 'rb') as f:
        r = subprocess.run(cmd, stdin=f, capture_output=True, timeout=900)
    return r.returncode == 0, (r.stderr.decode().strip() or '')

uploaded, skipped = [], []
try:
    c2 = connect()
    # 디렉터리는 /tmp 경유로 mkdir 후 root 권한으로 mv (rose90m이 직접 못 쓰는 영역)
    # rose90m이 쓸 수 있는 위치로 직접 mkdir 시도, 권한 없으면 sudo로
    sudo_run(c2, f"mkdir -p {NAS_BACKUP_DIR} && chmod 1777 {NAS_BACKUP_DIR}", timeout=30)
    c2.close()

    # SCP는 별도 subprocess (paramiko sudo와 분리)
    for lf in local_files:
        if not os.path.exists(lf):
            skipped.append(os.path.basename(lf)); continue
        rname = os.path.basename(lf)
        # 일단 /tmp로 업로드 후 sudo mv (rose90m이 NAS_BACKUP_DIR에 직접 못 쓸 가능성 대비)
        tmp_remote = f"/tmp/_nasbk_{rname}"
        sz = os.path.getsize(lf)
        ok, err = ssh_pipe_upload(lf, tmp_remote)
        if not ok:
            skipped.append(f"{rname} ({err[:80]})")
            print(f"  ❌ {rname} 업로드 실패: {err[:80]}")
            continue
        # sudo mv → 최종 위치
        c3 = connect()
        out, mv_err = sudo_run(c3, f"mv -f {tmp_remote} {NAS_BACKUP_DIR}/{rname} && chmod 644 {NAS_BACKUP_DIR}/{rname} && echo OK", timeout=30)
        c3.close()
        if 'OK' in out:
            uploaded.append((rname, sz))
            print(f"  ✅ {rname} ({sz // (1024*1024)}MB) → {NAS_BACKUP_DIR}/{rname}")
        else:
            skipped.append(f"{rname} (mv 실패: {mv_err[:80]})")
            print(f"  ❌ {rname} mv 실패: {mv_err[:80]}")

    c2 = connect()

    # ── 보존 정책: 각 파일 종류별 최신 NAS_KEEP_LAST개만 유지 ──
    print(f"  🧹 보존 정책 적용 (최신 {NAS_KEEP_LAST}개만 유지)...")
    patterns = [
        "attendance_backup_*.tar.gz",
        "attendance_db_FULL_*.sql",
        "attendance_docker_*.json",
        "attendance_dsm_config_*.dss",
        "attendance_crontab_*.txt",
        "attendance_launchagents_*.tar.gz",
    ]
    for pat in patterns:
        cmd = (f"ls -1t {NAS_BACKUP_DIR}/{pat} 2>/dev/null | "
               f"tail -n +{NAS_KEEP_LAST+1} | xargs -r rm -f -- ; echo OK")
        sudo_run(c2, cmd, timeout=20)
    c2.close()
    print(f"  ✅ NAS 사본 전송 완료 — {len(uploaded)}개 업로드, {len(skipped)}개 건너뜀")
except Exception as e:
    print(f"  ❌ NAS 전송 실패: {e}")

print()
print("=" * 50)
print("✅ 전체 백업 완료!")
print(f"  소스+chroma_db : attendance_backup_{TIMESTAMP}.tar.gz")
print(f"  DB             : attendance_db_FULL_{TIMESTAMP}.sql")
print(f"  Docker 설정    : attendance_docker_{TIMESTAMP}.json")
print(f"  DSM 설정       : attendance_dsm_config_{TIMESTAMP}.dss")
print(f"  crontab        : attendance_crontab_{TIMESTAMP}.txt")
print(f"  LaunchAgents   : attendance_launchagents_{TIMESTAMP}.tar.gz")
print(f"  📤 NAS 사본    : {NAS_HOST}:{NAS_BACKUP_DIR}/  (최신 {NAS_KEEP_LAST}개 유지)")
print()
print("💡 복구 방법:")
print("  Mac → 로컬: tar -xzf attendance_backup_*.tar.gz -C /Users/changkooji/")
print("  NAS → 다운로드: scp rose90m@{0}:{1}/<파일> ./".format(NAS_HOST, NAS_BACKUP_DIR))
print("  DB:   mysql -u root -p attendance < attendance_db_FULL_*.sql")
print("  cron: crontab attendance_crontab_*.txt")
