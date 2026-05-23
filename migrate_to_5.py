#!/usr/bin/env python3
"""
migrate_to_5.py — 근태관리시스템 마이그레이션: .11 NAS → .5 Linux 서버
  - attendance-app  컨테이너 (포트 5050)
  - attendance-tbm  컨테이너 (포트 5051)
  - attendance-db   컨테이너 (MariaDB, 포트 3307)  ← DB도 컨테이너로 이전
사용법: python migrate_to_5.py
"""
import paramiko, time, io, os, sys, tarfile, subprocess, re
from dotenv import load_dotenv

load_dotenv()

# ── 설정 ─────────────────────────────────────────────────────
NAS_HOST = "192.168.100.11"
NAS_USER = os.environ["NAS_USER"]
NAS_PASS = os.environ["NAS_PASS"]
DB_PASS  = os.environ["DB_PASSWORD"]
DB_USER  = "root"
DB_PORT  = "3307"
DB_NAME  = "attendance"

S5_HOST  = "192.168.100.5"
S5_USER  = NAS_USER
S5_PASS  = os.environ.get("SERVER5_PASS", NAS_PASS)
S5_KEY   = os.path.expanduser("~/.ssh/id_tams_nas")
STAGE    = f"/home/{NAS_USER}/attendance"


# ── SSH 헬퍼 ─────────────────────────────────────────────────
def nas_connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(NAS_HOST, username=NAS_USER, password=NAS_PASS, timeout=30)
    return c

def s5_connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(S5_HOST, username=S5_USER, key_filename=S5_KEY, timeout=30)
    return c

def ssh_run(c, cmd):
    _, out, _ = c.exec_command(cmd)
    return out.read().decode().strip()

def ssh_read_binary(c, cmd):
    """명령 stdout을 바이너리로 읽음 (SFTP 불필요)"""
    _, out, _ = c.exec_command(cmd)
    return out.read()

def sftp_put(c, data: bytes, remote_path: str):
    sftp = c.open_sftp()
    sftp.putfo(io.BytesIO(data), remote_path)
    sftp.close()


print("=" * 55)
print("🚀 근태관리시스템 마이그레이션: .11 NAS → .5 Linux")
print(f"   소스: {NAS_HOST}  →  대상: {S5_HOST}")
print("   DB도 컨테이너(attendance-db)로 이전")
print("=" * 55)


# ══════════════════════════════════════════════════════════
# PHASE A: NAS에서 데이터 수집 (SSH stdout 스트리밍)
# ══════════════════════════════════════════════════════════
print(f"\n[A] NAS 데이터 수집 ({NAS_HOST})...")
nas = nas_connect()

# A1: DB 덤프 — stdout으로 직접 gzip 스트림 읽기
print("  [A1] DB 덤프 중 (stdout 스트리밍)...")
DUMP_BIN = "/usr/local/bin/mysqldump"
_, out_chan, err_chan = nas.exec_command(
    f"MYSQL_PWD='{DB_PASS}' {DUMP_BIN} "
    f"-h 127.0.0.1 -P {DB_PORT} -u {DB_USER} "
    f"--single-transaction --routines --triggers {DB_NAME} | gzip"
)
dump_data = out_chan.read()
dump_err  = err_chan.read().decode().strip()
if dump_err:
    print(f"  ⚠️  dump stderr: {dump_err[:200]}")
if len(dump_data) < 100:
    print(f"  ❌ DB 덤프 실패 또는 빈 데이터 ({len(dump_data)}B)")
    print("     DB_PASSWORD 확인 필요")
    nas.close()
    sys.exit(1)
print(f"  ✅ DB 덤프 완료 ({len(dump_data)//1024}KB)")

# A2: uploads 폴더 — tar 스트리밍
print("  [A2] uploads 폴더 수집 중 (tar 스트리밍)...")
_, up_chan, _ = nas.exec_command(
    "tar czf - -C /volume1/docker/attendance uploads 2>/dev/null"
)
uploads_data = up_chan.read()
if len(uploads_data) > 100:
    print(f"  ✅ uploads 수집 완료 ({len(uploads_data)//1024}KB)")
else:
    uploads_data = b""
    print(f"  ⚠️  uploads 없음 — 건너뜀")

nas.close()
print("  NAS 연결 완료")


# ══════════════════════════════════════════════════════════
# PHASE B: 코드 번들 생성 (Mac local, git ls-files 기준)
# ══════════════════════════════════════════════════════════
print(f"\n[B] 코드 번들 생성 중...")
git_result = subprocess.run(['git', 'ls-files'], capture_output=True, text=True)
git_files = [f for f in git_result.stdout.strip().split('\n') if f and os.path.exists(f)]

buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode='w:gz') as tar:
    for f in git_files:
        tar.add(f, arcname=f)
    # .env 및 docker-compose.yml (gitignore 제외 가능)
    for extra in ['.env', 'docker-compose.yml']:
        if os.path.exists(extra) and extra not in git_files:
            tar.add(extra, arcname=extra)
code_data = buf.getvalue()
print(f"  ✅ 번들 완료 ({len(code_data)//1024}KB, {len(git_files)}개 파일)")


# ══════════════════════════════════════════════════════════
# PHASE C: .5 서버 배포
# ══════════════════════════════════════════════════════════
print(f"\n[C] .5 서버 배포 ({S5_HOST})...")
s5 = s5_connect()

# C1: 디렉토리 준비
ssh_run(s5, f"mkdir -p {STAGE}/static {STAGE}/templates/tbm "
            f"{STAGE}/uploads/documents {STAGE}/db-init")
print(f"  ✅ {STAGE} 준비")

# C2: 코드 전송
print("  [C2] 코드 전송 중...")
sftp_put(s5, code_data, "/tmp/_code.tar.gz")
out = ssh_run(s5, f"cd {STAGE} && tar xzf /tmp/_code.tar.gz && rm /tmp/_code.tar.gz && echo OK")
if "OK" not in out:
    print(f"  ❌ 코드 전송 실패: {out}")
    sys.exit(1)
print(f"  ✅ 코드 전송 완료")

# C3: uploads 전송
if uploads_data:
    print("  [C3] uploads 전송 중...")
    sftp_put(s5, uploads_data, "/tmp/uploads.tar.gz")
    out = ssh_run(s5, f"cd {STAGE} && tar xzf /tmp/uploads.tar.gz && rm /tmp/uploads.tar.gz && echo OK")
    print(f"  {'✅ uploads 전송 완료' if 'OK' in out else '⚠️  uploads 전송 실패: ' + out}")

# C4: DB 덤프 전송
print("  [C4] DB 덤프 전송 중...")
sftp_put(s5, dump_data, "/tmp/attendance_migrate.sql.gz")
print(f"  ✅ DB 덤프 전송 완료")

# C5: DB 컨테이너 시작
print("  [C5] DB 컨테이너 시작 중 (이미지 pull 포함)...")
out = ssh_run(s5, f"cd {STAGE} && docker compose up -d db 2>&1")
tail = out[-300:] if len(out) > 300 else out
print(f"    {tail}")

# C6: DB healthy 대기 (최대 90초)
print("  [C6] DB 준비 대기 중...")
for i in range(18):
    time.sleep(5)
    status = ssh_run(s5, "docker inspect --format='{{.State.Health.Status}}' attendance-db 2>/dev/null")
    print(f"    [{(i+1)*5}s] {status}")
    if status.strip() == "healthy":
        break
else:
    print("  ⚠️  타임아웃 — 15초 추가 대기 후 계속")
    time.sleep(15)

# C7: DB import — 컨테이너 자체 $MARIADB_ROOT_PASSWORD 사용 (이스케이프 문제 회피)
print("  [C7] DB 임포트 중...")
ssh_run(s5, "docker cp /tmp/attendance_migrate.sql.gz attendance-db:/tmp/")
out = ssh_run(s5,
    'docker exec attendance-db bash -c '
    '\'gunzip -c /tmp/attendance_migrate.sql.gz '
    f'| mariadb -u {DB_USER} -p"$MARIADB_ROOT_PASSWORD" {DB_NAME} && echo IMPORT_OK\'')
if "IMPORT_OK" not in out:
    print(f"  ❌ DB 임포트 실패: {out}")
    sys.exit(1)
print(f"  ✅ DB 임포트 완료")
ssh_run(s5, "rm -f /tmp/attendance_migrate.sql.gz")
ssh_run(s5, "docker exec attendance-db rm -f /tmp/attendance_migrate.sql.gz 2>/dev/null || true")

# C8: app + tbm 빌드 및 시작 (Dockerfile 빌드 포함 약 3분)
print("  [C8] app + tbm 컨테이너 빌드/시작 중 (약 3분)...")
out = ssh_run(s5, f"cd {STAGE} && docker compose up -d 2>&1")
tail = out[-500:] if len(out) > 500 else out
print(f"    {tail}")
print("  잠시 대기 중 (15초)...")
time.sleep(15)


# ══════════════════════════════════════════════════════════
# PHASE D: 헬스체크
# ══════════════════════════════════════════════════════════
print("\n[D] 헬스체크...")
main_code = ssh_run(s5, "curl -s -o /dev/null -w '%{http_code}' http://localhost:5050/")
tbm_code  = ssh_run(s5, "curl -s -o /dev/null -w '%{http_code}' http://localhost:5051/tbm/login")
containers = ssh_run(s5, "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'")
s5.close()

ok = main_code in ("200", "302") and tbm_code in ("200", "302")
print(f"  attendance-app (5050): {main_code} {'✅' if main_code in ('200','302') else '❌'}")
print(f"  attendance-tbm (5051): {tbm_code} {'✅' if tbm_code in ('200','302') else '❌'}")
print(f"\n  컨테이너 현황:\n{containers}")

# .env 자동 업데이트
if ok:
    try:
        with open(".env", encoding="utf-8") as f:
            content = f.read()
        content = re.sub(r"^NAS_HOST=.*$", f"NAS_HOST={S5_HOST}", content, flags=re.MULTILINE)
        if "NAS_STAGE_DIR=" in content:
            content = re.sub(r"^NAS_STAGE_DIR=.*$", f"NAS_STAGE_DIR={STAGE}", content, flags=re.MULTILINE)
        else:
            content += f"\nNAS_STAGE_DIR={STAGE}\n"
        with open(".env", "w", encoding="utf-8") as f:
            f.write(content)
        print(f"\n  ✅ .env 업데이트 (NAS_HOST → {S5_HOST})")
    except Exception as e:
        print(f"\n  ⚠️  .env 수동 업데이트 필요: NAS_HOST={S5_HOST}, NAS_STAGE_DIR={STAGE}")

print("\n" + "=" * 55)
if ok:
    print("✅ 마이그레이션 완료!")
    print(f"\n🌐 접속 테스트: http://{S5_HOST}:5050")
    print(f"\n다음 단계:")
    print(f"  1. .11 NAS 컨테이너 중지:")
    print(f"     ssh {NAS_USER}@{NAS_HOST}")
    print(f"     sudo docker stop attendance-app attendance-tbm")
    print(f"  2. caps_sync.py (Windows PC) DB 주소 변경:")
    print(f"     {NAS_HOST}:3307  →  {S5_HOST}:3307")
else:
    print("⚠️  일부 서비스 응답 없음 — 로그 확인:")
    print(f"   ssh {S5_USER}@{S5_HOST}")
    print(f"   docker logs attendance-app --tail 50")
    print(f"   docker logs attendance-tbm --tail 50")
print("=" * 55)
