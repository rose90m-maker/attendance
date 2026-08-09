#!/usr/bin/env python3
"""_prune_docker.py — NAS 도커 디스크 정리 (기본은 미리보기, 실제 삭제는 --apply)

2026-08-09 기준 NAS 상태: 빌드 캐시 14.33GB, 회수 가능 이미지 9.5GB.
디스크는 1.7T 여유라 급하지 않지만 계속 누적되는 구조라 정리한다.

■ 무엇을 지우는가 (attendance 관련만)
  1. attendance-*:buildtest      — 오늘 빌드 검증용 임시 이미지
  2. attendance-*:backup-*       — **가장 최근 것 1개는 남긴다** (롤백용)
  3. BuildKit 빌드 캐시          — docker builder prune

■ 무엇을 건드리지 않는가
  - :latest 및 실행 중인 컨테이너의 이미지
  - payroll-*, mosquitto, tailscale, dlg-* 등 다른 서비스 이미지
  - dangling(중간) 이미지 — NAS 는 **레거시 빌더**라 중간 이미지가 곧 빌드 캐시다.
    이걸 지우면 다음 재빌드가 chromium 부터 다시 받아 20분 더 걸린다.
    그래서 `docker image prune` 은 **일부러 쓰지 않는다.**

사용:
  python3 _archive/_prune_docker.py           미리보기 (아무것도 안 지움)
  python3 _archive/_prune_docker.py --apply   실제 삭제
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

APPLY = "--apply" in sys.argv
IMAGES = ["attendance-app", "attendance-tbm"]

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(os.environ["NAS_HOST"], port=22, username=os.environ["NAS_USER"],
          password=os.environ["NAS_PASS"], timeout=30)


def sudo_nas(cmd, timeout=600):
    payload = base64.b64encode(
        f"PATH=/usr/local/bin:$PATH\n{cmd}\n".encode("utf-8")).decode("ascii")
    full = (f"echo '{os.environ['NAS_SUDO']}' | sudo -S "
            f"sh -c \"echo {payload} | base64 -d | sh\" 2>&1")
    _, o, _ = c.exec_command(full, timeout=timeout)
    return re.sub(r'^Password:\s*', '', o.read().decode("utf-8", "replace")).strip()


def head(t):
    print(f"\n{'=' * 64}\n  {t}\n{'=' * 64}")


mode = "실제 삭제 (--apply)" if APPLY else "미리보기 — 아무것도 지우지 않습니다"
print(f"모드: {mode}")

head("정리 전 디스크 사용량")
before = sudo_nas("docker system df")
print(before)

# ── 지울 대상 선정 ───────────────────────────────────────────
targets = []

for img in IMAGES:
    tags = sudo_nas(f"docker images {img} --format '{{{{.Tag}}}}'").splitlines()
    tags = [t.strip() for t in tags if t.strip()]

    buildtest = [t for t in tags if t == "buildtest"]
    backups = sorted([t for t in tags if t.startswith("backup-")], reverse=True)

    # 가장 최근 백업 1개는 롤백을 위해 반드시 남긴다.
    keep = backups[0] if backups else None
    stale = backups[1:]

    for t in buildtest:
        targets.append(f"{img}:{t}")
    for t in stale:
        targets.append(f"{img}:{t}")

    print(f"\n[{img}]")
    print(f"  보존   :latest" + (f", :{keep} (최신 백업 — 롤백용)" if keep else ""))
    print(f"  삭제   {', '.join(':' + t for t in buildtest + stale) or '(없음)'}")

head("삭제 대상 이미지 태그")
if targets:
    for t in targets:
        print(f"  - {t}")
else:
    print("  (없음)")

# ── 실행 ─────────────────────────────────────────────────────
if not APPLY:
    print("\n미리보기입니다. 실제로 지우려면 --apply 를 붙여 다시 실행하세요:")
    print("  python3 _archive/_prune_docker.py --apply")
    c.close()
    sys.exit(0)

head("이미지 태그 삭제")
if targets:
    # 태그만 지운다. 같은 이미지 ID 를 :latest 가 참조하면 실체는 남는다.
    out = sudo_nas("docker rmi " + " ".join(targets) + " ; echo RMI_EXIT_$?")
    print(out)
else:
    print("삭제할 태그 없음")

head("BuildKit 빌드 캐시 삭제")
print("레거시 빌더의 중간 이미지는 건드리지 않습니다 (다음 재빌드 속도 유지).")
print(sudo_nas("docker builder prune -f ; echo PRUNE_EXIT_$?", timeout=900))

head("정리 후 디스크 사용량")
print(sudo_nas("docker system df"))

head("컨테이너 상태 확인 (영향 없어야 정상)")
print(sudo_nas("docker ps --filter name=attendance "
               "--format '{{.Names}} | {{.Status}} | {{.Image}}'"))

print("\n정리 완료. 컨테이너가 둘 다 Up 이면 이상 없습니다.")
c.close()
