"""
NAS 컨테이너 사전 점검 — 9단계(app_maria.py 수정) 진입 전 일회용 스크립트.
.env에서 자격증명 읽음. 비밀번호 노출 없음. 값 노출 없음 (키만).
실행 후 _archive/로 이동.
"""
import os
import paramiko
from dotenv import load_dotenv

load_dotenv()

NAS_HOST = os.environ["NAS_HOST"]
NAS_USER = os.environ["NAS_USER"]
NAS_PASS = os.environ["NAS_PASS"]
NAS_SUDO = os.environ["NAS_SUDO"]

# 점검할 컨테이너 이름
CONTAINERS = ["attendance-app", "attendance-tbm"]

# 우리가 .env에 추가한 11개 키
ADDED_KEYS = [
    "FLASK_SECRET_KEY", "TBM_SECRET_KEY",
    "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID",
    "MAIL_USER", "MAIL_PASS",
    "NAS_HOST", "NAS_USER", "NAS_PASS", "NAS_SUDO",
    "CAPS_MDB_PWD",
]


def run(c, cmd, timeout=30):
    """sudo로 명령 실행. docker는 NAS에서 sudo 필수."""
    stdin, stdout, stderr = c.exec_command(f"sudo -S {cmd}", timeout=timeout)
    stdin.write(NAS_SUDO + "\n"); stdin.flush()
    out = stdout.read().decode()
    # sudo 프롬프트 'Password: ' 제거
    out = out.replace("Password: ", "").strip()
    return out, stderr.read().decode().strip()


def main():
    print("=" * 60)
    print(f"🔍 NAS 컨테이너 사전 점검 ({NAS_HOST})")
    print("=" * 60)

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(NAS_HOST, port=22, username=NAS_USER, password=NAS_PASS, timeout=10)
    print(f"✅ SSH 접속 성공 ({NAS_USER}@{NAS_HOST})\n")

    # ── 1. 컨테이너 상태 ─────────────────────────────────────
    print("─" * 60)
    print("[1] 컨테이너 상태")
    print("─" * 60)
    out, _ = run(c, "/usr/local/bin/docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}' | grep -E 'attendance'")
    for line in out.splitlines():
        print(f"  {line}")
    print()

    # ── 2~4. 컨테이너별 점검 ─────────────────────────────────
    for cname in CONTAINERS:
        print("─" * 60)
        print(f"[Container: {cname}]")
        print("─" * 60)

        # 2-A: 실행 중인지
        status_out, _ = run(c, f"/usr/local/bin/docker inspect -f '{{{{.State.Running}}}}' {cname} 2>/dev/null")
        running = status_out.strip() == "true"
        print(f"  실행 상태: {'✅ running' if running else '❌ stopped'}")

        if not running:
            print(f"  ⚠️  {cname} 미실행 — 후속 점검 스킵\n")
            continue

        # 2-B: python-dotenv 설치 여부
        pip_out, _ = run(c, f"/usr/local/bin/docker exec {cname} pip show python-dotenv 2>/dev/null | head -3")
        if "Name:" in pip_out:
            ver_line = [l for l in pip_out.splitlines() if l.startswith("Version:")]
            ver = ver_line[0] if ver_line else "(버전 정보 없음)"
            print(f"  python-dotenv: ✅ 설치됨 ({ver})")
        else:
            print(f"  python-dotenv: ❌ 미설치")

        # 2-C: /app/.env 파일 존재
        env_out, _ = run(c, f"/usr/local/bin/docker exec {cname} sh -c 'ls -la /app/.env 2>&1 | head -1'")
        if "No such file" in env_out or "cannot access" in env_out:
            print(f"  /app/.env: ❌ 없음")
        else:
            # 권한과 크기만 표시 (내용은 노출 X)
            parts = env_out.split()
            if len(parts) >= 5:
                print(f"  /app/.env: ✅ 존재 (권한={parts[0]}, 크기={parts[4]}B)")
            else:
                print(f"  /app/.env: ✅ 존재 ({env_out})")

        # 2-D: 환경변수 키 목록 (값 노출 X)
        env_keys_out, _ = run(
            c,
            f"/usr/local/bin/docker exec {cname} sh -c \"env | awk -F= '{{print \\$1}}' | sort\""
        )
        all_keys = set(env_keys_out.splitlines())
        # 우리가 관심있는 11개 키 + DB_PASSWORD 정도만 check
        relevant_keys = ADDED_KEYS + ["DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_USER", "DB_NAME"]
        present = [k for k in relevant_keys if k in all_keys]
        missing = [k for k in relevant_keys if k not in all_keys]
        print(f"  컨테이너 환경변수 (관심 키 {len(relevant_keys)}개 중):")
        print(f"    ✅ 존재: {len(present)}개 — {present}")
        print(f"    ❌ 없음: {len(missing)}개 — {missing}")

        # 2-E: 마운트 정보 (.env 등)
        mounts_out, _ = run(
            c,
            f"/usr/local/bin/docker inspect -f '{{{{range .Mounts}}}}{{{{.Source}}}} -> {{{{.Destination}}}}{{{{\"\\n\"}}}}{{{{end}}}}' {cname}"
        )
        print(f"  마운트:")
        for line in mounts_out.splitlines():
            if line.strip():
                print(f"    {line}")

        print()

    c.close()
    print("=" * 60)
    print("✅ 점검 완료")
    print("=" * 60)


if __name__ == "__main__":
    main()
