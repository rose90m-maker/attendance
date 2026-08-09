#!/usr/bin/env python3
"""_check_build.py — 이미지 빌드만 재현해서 실패 지점을 잡는다 (일회용 디버그)

`deploy_and_restart.py --rebuild` 가 rebuild_containers.py 에 위임하고 실패했는데,
어느 단계에서 넘어졌는지 출력이 남지 않았다. 그래서 빌드만 따로 돌려 전문을 받는다.

■ 안전성
  - 실행하는 것은 `docker build` 하나뿐이다.
  - 태그를 `:buildtest` 로 붙이므로 `:latest` 를 건드리지 않는다.
  - stop / rm / run / restart 를 하지 않는다. 다운타임 없음.
  - 실패해도 현재 서비스는 그대로다.

■ 부작용
  - `attendance-app:buildtest`, `attendance-tbm:buildtest` 이미지가 생긴다.
    확인 후 지우려면:  docker rmi attendance-app:buildtest attendance-tbm:buildtest
  - 빌드 캐시가 쌓인다 (디스크 1.7T 여유이므로 문제 없음).
  - 빌드에 수 분 걸린다. playwright/chromium 단계가 특히 오래 걸린다.

사용:  python3 _check_build.py
"""
import base64
import os
import re
import sys
import time

try:
    import paramiko
except ImportError:
    sys.exit("paramiko 가 없습니다.  pip install paramiko  후 다시 실행하세요.")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

need = ["NAS_HOST", "NAS_USER", "NAS_PASS", "NAS_SUDO"]
missing = [k for k in need if not os.environ.get(k)]
if missing:
    sys.exit(f".env 에 다음 키가 없습니다: {', '.join(missing)}")

NAS_HOST = os.environ["NAS_HOST"]
NAS_USER = os.environ["NAS_USER"]
NAS_PASS = os.environ["NAS_PASS"]
NAS_SUDO = os.environ["NAS_SUDO"]
STAGE = os.environ.get("NAS_STAGE_DIR", "/volume1/web/attendance")
DOCKER = "/usr/local/bin/docker"

_SECRETS = [v for v in (NAS_PASS, NAS_SUDO, os.environ.get("DB_PASSWORD", "")) if v]


def mask(text: str) -> str:
    for s in _SECRETS:
        text = text.replace(s, "***")
    return re.sub(
        r'((?:password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*)(["\']?)([^\s"\',}]+)(\2)',
        lambda m: f"{m.group(1)}{m.group(2)}***{m.group(4)}", text, flags=re.I)


OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_build.txt")
_out = open(OUT_PATH, "w", encoding="utf-8")


def say(text=""):
    print(text)
    _out.write(str(text) + "\n")
    _out.flush()


c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    c.connect(NAS_HOST, port=22, username=NAS_USER, password=NAS_PASS, timeout=30)
except Exception as e:
    sys.exit(f"NAS 접속 실패: {type(e).__name__}: {e}")

say(f"접속: {NAS_USER}@{NAS_HOST}   스테이징: {STAGE}")


def sudo_nas(cmd: str, timeout: int = 3000) -> str:
    """따옴표 중첩을 피하려고 명령을 base64 로 실어 보낸다."""
    payload = base64.b64encode(
        f"PATH=/usr/local/bin:$PATH\n{cmd}\n".encode("utf-8")
    ).decode("ascii")
    full = f"echo '{NAS_SUDO}' | sudo -S sh -c \"echo {payload} | base64 -d | sh\" 2>&1"
    _, o, _ = c.exec_command(full, timeout=timeout)
    return re.sub(r'^Password:\s*', '', o.read().decode("utf-8", "replace"))


# ── 사전 정보 ────────────────────────────────────────────────
say("\n" + "=" * 68)
say("  docker 버전 · 빌더 · 캐시")
say("=" * 68)
say(mask(sudo_nas("docker version --format 'Server {{.Server.Version}} / API {{.Server.APIVersion}}'")).strip())
say(mask(sudo_nas("docker system df")).strip())

say("\n" + "=" * 68)
say("  빌드 컨텍스트 크기 (1.7GB 이미지의 원인 추적)")
say("=" * 68)
say(mask(sudo_nas(f"du -sh {STAGE} 2>/dev/null; ls {STAGE}/.dockerignore 2>&1")).strip())

# ── 빌드 ─────────────────────────────────────────────────────
# :latest 가 아니라 :buildtest 로 태그해서 운영 이미지를 건드리지 않는다.
for img, df in [("attendance-app", "Dockerfile"), ("attendance-tbm", "Dockerfile.tbm")]:
    say("\n" + "=" * 68)
    say(f"  {img} 빌드 ({df}) → {img}:buildtest")
    say("=" * 68)
    say("빌드 중… 수 분 걸립니다. 기다려 주세요.")
    t0 = time.time()
    out = sudo_nas(
        f"cd {STAGE} && {DOCKER} build -f {df} -t {img}:buildtest . ; echo BUILD_EXIT_$?",
        timeout=3000,
    )
    elapsed = int(time.time() - t0)

    m = re.search(r'BUILD_EXIT_(\d+)', out)
    code = m.group(1) if m else "?"
    say(f"\n── 종료 코드: {code}   소요: {elapsed}초 ──")

    if code == "0":
        say("✅ 빌드 성공")
        say("\n-- 마지막 15줄 --")
        say(mask("\n".join(out.splitlines()[-15:])))
    else:
        say("❌ 빌드 실패 — 전체 출력:")
        say(mask(out))

    # rebuild_containers.py:121 의 문자열 판정이 이 출력에 어떻게 반응하는지 확인
    hit = [k for k in ("Successfully", "writing image", "DONE") if k in out]
    say(f"\n[참고] rebuild_containers.py 의 성공 판정 키워드 매칭: "
        f"{hit if hit else '없음 → 성공해도 실패로 오판함'}")

c.close()
_out.close()
print(f"\n>>> 전체 출력이 {OUT_PATH} 에 저장되었습니다.")
print("    빌드 테스트 이미지 삭제:  docker rmi attendance-app:buildtest attendance-tbm:buildtest")
