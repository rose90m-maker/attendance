#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""이미지 재빌드 + 컨테이너 재생성

`deploy_and_restart.py --rebuild` 는 이 NAS 에서 동작하지 않는다.
`docker compose`(공백형) 를 쓰는데 Synology 에는 그 명령이 없어서 조용히 실패하고,
스크립트는 성공으로 표시한다. 그래서 이미지가 2026-05-26 자에 멈춰 있고,
새 파이썬 패키지는 컨테이너에 직접 pip install 해 왔다(재생성하면 소실).

이 스크립트는 `docker build` 로 이미지를 만들고 컨테이너를 다시 만든다.
현재 실행 중인 설정(포트·볼륨·재시작정책·CMD)을 그대로 재현한다.

⚠️ CMD 를 반드시 명시한다. 이미지 기본 CMD 는 gunicorn 인데 운영은
   `python app_maria.py` 로 돌고 있다. 생략하면 실행 방식이 바뀐다.
   (2026-05-19 사고: tbm 이 CMD 없이 떠서 app_maria.py 로 기동)

실행:
    python rebuild_containers.py --check    # 준비 상태만 점검
    python rebuild_containers.py            # 실제 재빌드 + 재생성
    python rebuild_containers.py --rollback # 직전 이미지로 되돌리기
"""
import argparse
import os
import sys
import time

import paramiko
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

DOCKER = "/usr/local/bin/docker"
STAGE = os.environ.get("NAS_STAGE_DIR", "/volume1/web/attendance")
ENV_FILE = f"{STAGE}/.env"
UPLOADS = "/volume1/docker/attendance/uploads:/app/uploads"

CONTAINERS = [
    {"name": "attendance-app", "image": "attendance-app", "port": "5050:5050",
     "cmd": "python app_maria.py", "health": "http://localhost:5050/"},
    {"name": "attendance-tbm", "image": "attendance-tbm", "port": "5051:5051",
     "cmd": "python tbm_app.py", "health": "http://localhost:5051/tbm/login",
     "dockerfile": "Dockerfile.tbm"},
]

_cli = None


def ssh():
    global _cli
    if _cli is None:
        _cli = paramiko.SSHClient()
        _cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        _cli.connect(os.environ["NAS_HOST"], username=os.environ["NAS_USER"],
                     password=os.environ["NAS_PASS"], timeout=30)
    return _cli


def sudo(cmd, t=1800):
    """NAS sudo 실행.

    deploy_and_restart.py 와 같은 형태를 쓴다 — 비밀번호는 작은따옴표,
    명령은 `sh -c` 로 감싼다. sudo 에 `cd A && B` 를 그냥 넘기면
    cd 가 먹지 않아 뒤 명령이 통째로 실행되지 않는다.
    """
    full = (f"echo '{os.environ['NAS_SUDO']}' | sudo -S "
            f"sh -c 'PATH=/usr/local/bin:$PATH {cmd}' 2>&1")
    _, o, _ = ssh().exec_command(full, timeout=t)
    out = o.read().decode().strip()
    return out[len("Password: "):].strip() if out.startswith("Password: ") else out


def plain(cmd, t=60):
    _, o, _ = ssh().exec_command(cmd, timeout=t)
    return o.read().decode().strip()


def health(url, tries=12):
    """서비스가 응답할 때까지 기다린다 (NAS 안에서 sudo 없이 curl)"""
    for _ in range(tries):
        code = plain(f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 {url}")
        if code in ("200", "302", "404"):
            return code
        time.sleep(5)
    return code


def check():
    print("── 준비 상태 점검 ──")
    print("Dockerfile      :", "있음" if os.path.exists("Dockerfile") else "없음")
    print("Dockerfile.tbm  :", "있음" if os.path.exists("Dockerfile.tbm") else "없음")
    # .env 는 배포 사용자 권한으로 읽힌다 (sudo 불필요)
    print("NAS .env        :", plain(f"test -f {ENV_FILE} && echo 있음 || echo 없음"))
    print("  ERP 키        :", plain(f"grep -c '^ERP_DB_' {ENV_FILE}") or "0", "개")
    print("  DB 키         :", plain(f"grep -c '^DB_' {ENV_FILE}") or "0", "개")
    print("현재 이미지     :")
    print(sudo(f'{DOCKER} images | grep -E "attendance|REPO"'))
    print("현재 컨테이너   :")
    print(sudo(f'{DOCKER} ps --filter name=attendance '
               f'--format "{{{{.Names}}}} {{{{.Status}}}} {{{{.Ports}}}}"'))
    print("디스크 여유     :", plain("df -h /volume1 | tail -1"))


def rebuild(only=None):
    ts = time.strftime("%Y%m%d%H%M")
    for c in CONTAINERS:
        if only and c["name"] != only:
            continue
        name, img = c["name"], c["image"]
        df = c.get("dockerfile", "Dockerfile")
        print(f"\n{'='*56}\n{name}\n{'='*56}")

        # 1) 롤백용으로 현재 이미지에 태그를 남긴다
        print(f"  [1/5] 현재 이미지 백업 태그 → {img}:backup-{ts}")
        print("       ", sudo(f"{DOCKER} tag {img}:latest {img}:backup-{ts}") or "OK")

        # 2) 빌드 (컨테이너는 그대로 돌아간다 — 여기까진 무중단)
        print(f"  [2/5] 이미지 빌드 ({df}) — 수 분 소요")
        out = sudo(f"cd {STAGE} && {DOCKER} build -f {df} -t {img}:latest .", t=2400)
        tail = "\n".join(out.splitlines()[-6:])
        print("       ", tail)
        if "Successfully" not in out and "writing image" not in out and "DONE" not in out:
            print("  ❌ 빌드 실패로 판단 — 중단합니다 (컨테이너는 그대로)")
            return False

        # 3) 정지·삭제 → 여기서부터 다운타임
        print("  [3/5] 컨테이너 재생성 (다운타임 시작)")
        sudo(f"{DOCKER} stop {name}")
        sudo(f"{DOCKER} rm {name}")

        # 4) 생성 — CMD 명시 필수
        run = (f"{DOCKER} run -d --name {name} --restart unless-stopped "
               f"-p {c['port']} -v {UPLOADS} --env-file {ENV_FILE} "
               f"{img}:latest {c['cmd']}")
        print("       ", sudo(run)[:80])

        # 5) 헬스체크
        code = health(c["health"])
        print(f"  [5/5] 헬스체크 {c['health']} → {code}")
        if code not in ("200", "302", "404"):
            print(f"  ❌ 기동 실패. 로그:\n{sudo(f'{DOCKER} logs --tail 30 {name}')}")
            print(f"  ↩︎  롤백: python rebuild_containers.py --rollback")
            return False
        print(f"  ✅ {name} 정상")
    return True


def rollback():
    print("── 롤백: 가장 최근 backup-* 이미지로 되돌립니다 ──")
    for c in CONTAINERS:
        name, img = c["name"], c["image"]
        tags = sudo(f'{DOCKER} images {img} --format "{{{{.Tag}}}}" | grep backup- | sort -r')
        tag = tags.splitlines()[0].strip() if tags else ""
        if not tag:
            print(f"  {name}: 백업 이미지 없음 — 건너뜀")
            continue
        print(f"  {name}: {img}:{tag} 로 복구")
        sudo(f"{DOCKER} stop {name}; {DOCKER} rm {name}")
        sudo(f"{DOCKER} tag {img}:{tag} {img}:latest")
        run = (f"{DOCKER} run -d --name {name} --restart unless-stopped "
               f"-p {c['port']} -v {UPLOADS} --env-file {ENV_FILE} "
               f"{img}:latest {c['cmd']}")
        sudo(run)
        print(f"    헬스체크 → {health(c['health'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="준비 상태만 점검")
    ap.add_argument("--rollback", action="store_true", help="직전 이미지로 복구")
    ap.add_argument("--only", help="컨테이너 하나만 (attendance-app / attendance-tbm)")
    args = ap.parse_args()

    if args.check:
        check()
        return 0
    if args.rollback:
        rollback()
        return 0

    print("⚠️  컨테이너를 재생성합니다. 컨테이너당 30초~1분 다운타임이 발생합니다.")
    check()
    print("\n계속하려면 5초 안에 Ctrl+C 로 중단하지 마세요…")
    time.sleep(5)
    ok = rebuild(args.only)
    print("\n" + ("✅ 완료" if ok else "❌ 실패 — 위 로그를 확인하세요"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
