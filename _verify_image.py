#!/usr/bin/env python3
"""_verify_image.py — 새로 빌드한 :buildtest 이미지가 멀쩡한지 확인 (읽기 전용)

freetds-dev 를 Dockerfile 에서 제거했으므로, pymssql 이 정말 동작하는지
운영에 올리기 **전에** 확인한다. ERP 연동 8개 파일과 원천징수영수증이 여기 걸려 있다.

■ 안전성
  - `docker run --rm` 으로 임시 컨테이너를 띄워 명령 하나 실행하고 즉시 지운다.
  - 포트를 열지 않고, 볼륨도 붙이지 않고, DB 에 접속하지 않는다.
  - 운영 컨테이너 attendance-app / attendance-tbm 은 건드리지 않는다.

사용:  python3 _verify_image.py
"""
import base64
import os
import re
import sys

try:
    import paramiko
except ImportError:
    sys.exit("paramiko 가 없습니다.  pip install paramiko  후 다시 실행하세요.")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

for k in ("NAS_HOST", "NAS_USER", "NAS_PASS", "NAS_SUDO"):
    if not os.environ.get(k):
        sys.exit(f".env 에 {k} 가 없습니다.")

IMG = "attendance-app:buildtest"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(os.environ["NAS_HOST"], port=22, username=os.environ["NAS_USER"],
          password=os.environ["NAS_PASS"], timeout=30)


def sudo_nas(cmd, timeout=180):
    payload = base64.b64encode(
        f"PATH=/usr/local/bin:$PATH\n{cmd}\n".encode("utf-8")).decode("ascii")
    full = (f"echo '{os.environ['NAS_SUDO']}' | sudo -S "
            f"sh -c \"echo {payload} | base64 -d | sh\" 2>&1")
    _, o, _ = c.exec_command(full, timeout=timeout)
    return re.sub(r'^Password:\s*', '', o.read().decode("utf-8", "replace")).strip()


def run_in_image(shell_cmd, timeout=180):
    """이미지 안에서 명령 하나 실행 후 컨테이너 즉시 삭제."""
    b64 = base64.b64encode(shell_cmd.encode("utf-8")).decode("ascii")
    return sudo_nas(
        f"docker run --rm --entrypoint sh {IMG} -c "
        f"'echo {b64} | base64 -d | sh'", timeout=timeout)


def check(title, cmd, ok_marker):
    out = run_in_image(cmd)
    passed = ok_marker in out
    print(f"\n{'✅' if passed else '❌'} {title}")
    for line in out.splitlines()[:6]:
        print("    ", line)
    return passed


print(f"검증 대상 이미지: {IMG}")
print("임시 컨테이너를 띄워 확인합니다. 운영 컨테이너는 건드리지 않습니다.")

results = {}

# ── 1. pymssql — freetds-dev 제거의 핵심 검증 ────────────────
results["pymssql"] = check(
    "pymssql (ERP 연동 · 원천징수영수증)",
    'python -c "import pymssql; print(\'PYMSSQL_OK\', pymssql.__version__)"',
    "PYMSSQL_OK")

# ── 2. playwright + chromium — 원천징수영수증 PDF ────────────
results["playwright"] = check(
    "playwright + chromium (영수증 PDF 변환)",
    'python -c "'
    'from playwright.sync_api import sync_playwright; '
    'p=sync_playwright().start(); '
    'b=p.chromium.launch(); b.close(); p.stop(); '
    'print(\'CHROMIUM_OK\')"',
    "CHROMIUM_OK")

# ── 3. 나눔폰트 — 영수증 한글 렌더링 ─────────────────────────
results["fonts"] = check(
    "나눔폰트 (영수증 한글 렌더링)",
    'fc-list 2>/dev/null | grep -i nanum | head -2 && echo FONT_OK',
    "FONT_OK")

# ── 4. 앱 모듈 임포트 ────────────────────────────────────────
results["app"] = check(
    "주요 모듈 임포트",
    'cd /app && python -c "'
    'import pymysql, flask, holidays, openpyxl, reportlab; '
    'print(\'IMPORTS_OK\')"',
    "IMPORTS_OK")

# ── 5. gunicorn 존재 확인 ────────────────────────────────────
results["gunicorn"] = check(
    "gunicorn (이미지 기본 CMD 가 사용)",
    'gunicorn --version && echo GUNICORN_OK',
    "GUNICORN_OK")

# ── 6. 보안: .env 가 이미지에 구워졌는가 ─────────────────────
print("\n" + "─" * 60)
env_out = run_in_image('ls -l /app/.env 2>&1 | head -2; echo ---; '
                       'grep -c . /app/.env 2>/dev/null || echo 0')
baked = "/app/.env" in env_out and "No such file" not in env_out
print(f"{'⚠️ ' if baked else '✅'} 이미지에 .env 포함 여부: "
      f"{'포함됨 — 자격증명이 이미지 레이어에 구워졌습니다' if baked else '없음 (정상)'}")
for line in env_out.splitlines()[:4]:
    print("    ", line)

# ── 요약 ─────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  요약")
print("=" * 60)
for k, v in results.items():
    print(f"  {'✅' if v else '❌'} {k}")

if all(results.values()):
    print("\n전부 통과했습니다. Dockerfile 수정본으로 실제 재빌드를 진행해도 됩니다.")
else:
    fail = [k for k, v in results.items() if not v]
    print(f"\n실패: {', '.join(fail)} — 재빌드하지 말고 결과를 알려주세요.")

c.close()
