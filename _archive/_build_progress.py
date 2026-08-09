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


def snapshot():
    """레거시 빌더는 BuildKit 캐시를 안 쓰고 중간 이미지를 만든다.
    그래서 진행 신호는 캐시 크기가 아니라 이미지(레이어) 개수다."""
    out = sudo_nas(
        "docker images -a | wc -l; "
        "docker images --format '{{.Repository}}:{{.Tag}}' | grep -c buildtest; "
        "ps -ef 2>/dev/null | grep -E 'docker build' | grep -v grep | wc -l"
    )
    nums = [x.strip() for x in out.splitlines() if x.strip().isdigit()]
    while len(nums) < 3:
        nums.append("0")
    return int(nums[0]), int(nums[1]), int(nums[2])


print("빌드 진행 확인 — 15초 간격 4회. Ctrl+C 로 중단해도 빌드에는 영향 없습니다.\n")

prev = None
for i in range(4):
    layers, done, running = snapshot()
    delta = "" if prev is None else f"  (직전 대비 +{layers - prev})"
    prev = layers
    print(f"[{i+1}/4] 레이어 {layers}개{delta}   "
          f"완성된 buildtest 이미지 {done}개   "
          f"docker build 프로세스 {'실행 중' if running else '없음'}")
    if i < 3:
        time.sleep(15)

print()
print("판단:")
print("  · 레이어 개수가 늘고 있다        → 정상 진행 중")
print("  · 개수 그대로 + 프로세스 실행 중 → 오래 걸리는 단계 (chromium 등). 정상")
print("  · 프로세스 없음 + buildtest 2개  → 빌드 완료. 원래 창을 보세요")
print("  · 프로세스 없음 + buildtest 0개  → 실패했을 수 있습니다. 알려주세요")

c.close()
