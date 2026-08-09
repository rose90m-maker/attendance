#!/usr/bin/env python3
"""_build_progress.py — 빌드가 실제로 진행 중인지 확인 (읽기 전용)

_check_build.py 가 돌아가는 동안 진행 상황이 안 보여서 만든 확인용.
**새 터미널 창**에서 실행하세요. 원래 빌드에는 아무 영향이 없습니다.

사용:  python3 _build_progress.py
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

for k in ("NAS_HOST", "NAS_USER", "NAS_PASS", "NAS_SUDO"):
    if not os.environ.get(k):
        sys.exit(f".env 에 {k} 가 없습니다.")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(os.environ["NAS_HOST"], port=22, username=os.environ["NAS_USER"],
          password=os.environ["NAS_PASS"], timeout=30)


def sudo_nas(cmd, timeout=60):
    payload = base64.b64encode(
        f"PATH=/usr/local/bin:$PATH\n{cmd}\n".encode("utf-8")).decode("ascii")
    full = (f"echo '{os.environ['NAS_SUDO']}' | sudo -S "
            f"sh -c \"echo {payload} | base64 -d | sh\" 2>&1")
    _, o, _ = c.exec_command(full, timeout=timeout)
    return re.sub(r'^Password:\s*', '', o.read().decode("utf-8", "replace"))


def cache_bytes():
    """빌드 캐시 크기를 바이트로. 늘어나면 빌드가 전진하고 있다는 뜻."""
    out = sudo_nas("docker system df --format '{{.Type}}|{{.Size}}'")
    for line in out.splitlines():
        if line.startswith("Build Cache"):
            raw = line.split("|", 1)[1].strip()
            m = re.match(r'([\d.]+)\s*([KMGT]?i?B)', raw)
            if not m:
                return None, raw
            n, unit = float(m.group(1)), m.group(2)
            mult = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12,
                    "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
            return n * mult.get(unit, 1), raw
    return None, "(못 찾음)"


print("빌드 진행 확인 — 20초 간격으로 5회 봅니다. Ctrl+C 로 언제든 중단하세요.\n")

prev = None
for i in range(5):
    procs = sudo_nas("ps -ef 2>/dev/null | grep -E 'docker[- ]build|buildkit|apt-get|dpkg' "
                     "| grep -v grep | head -4").strip()
    size, raw = cache_bytes()

    delta = ""
    if prev is not None and size is not None:
        diff = size - prev
        delta = f"  (직전 대비 {'+' if diff >= 0 else ''}{diff/1e6:.1f}MB)"
    prev = size

    print(f"[{i+1}/5] 빌드 캐시 {raw}{delta}")
    if procs:
        print("       진행 중인 작업:")
        for line in procs.splitlines():
            print("        ", line.strip()[:150])
    else:
        print("       docker build / apt 프로세스 안 보임")
    print()

    if i < 4:
        time.sleep(20)

c.close()
print("판단:")
print("  · 캐시 크기가 늘고 있다  → 정상 진행 중. 계속 기다리세요.")
print("  · 크기 그대로 + apt/dpkg 프로세스 보임 → 패키지 설치 중. 정상입니다.")
print("  · 크기 그대로 + 아무 프로세스 없음 → 멈췄을 수 있습니다. 알려주세요.")
