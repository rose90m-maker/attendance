#!/usr/bin/env python3
"""_verify_msgfav.py — 즐겨찾기 기능이 운영에 반영됐는지 확인 (읽기 전용)

배포 로그는 파일 전송까지만 말해준다. 컨테이너 안에 `app.py`(옛것)와
`app_maria.py`(배포본)가 둘 다 있어서, **gunicorn 이 어느 쪽을 실행하는지**가
반영 여부를 가른다. 그것부터 본다.
"""
import os
import re
import sys

from dotenv import load_dotenv
load_dotenv()

import paramiko

host = os.environ["NAS_HOST"]
user = os.environ["NAS_USER"]
sudo = os.environ["NAS_SUDO"]

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ["NAS_PASS"], timeout=15)


def run(cmd):
    _i, o, e = c.exec_command(cmd, timeout=60)
    out = o.read().decode("utf-8", "replace") + e.read().decode("utf-8", "replace")
    return re.sub(r"^Password:\s*", "", out).strip()


DOCKER = "/usr/local/bin/docker"


def dexec(cmd):
    return run(f"echo '{sudo}' | sudo -S {DOCKER} exec attendance-app sh -c \"{cmd}\"")


print("== gunicorn 이 실행 중인 모듈 ==")
top = run(f"echo '{sudo}' | sudo -S {DOCKER} top attendance-app")
for ln in top.splitlines():
    if "gunicorn" in ln or "python" in ln:
        print("  " + ln.strip()[-150:])

print("\n== 이미지에 박힌 CMD ==")
cmd = run(f"echo '{sudo}' | sudo -S {DOCKER} inspect attendance-app "
          f"--format '{{{{json .Config.Cmd}}}} {{{{json .Path}}}} {{{{json .Args}}}}'")
print("  " + cmd.splitlines()[-1])

print("\n== 파일별 즐겨찾기 코드 유무 ==")
for f in ("/app/app.py", "/app/app_maria.py"):
    n = dexec(f"grep -c api_msg_fav {f} 2>/dev/null || echo 0").splitlines()[-1]
    print(f"  {f:<22}{n:>4}")

print("\n== 파일 시각 ==")
ls = dexec("ls -l --time-style=+%m-%d_%H:%M /app/app.py /app/app_maria.py "
           "/app/templates/message_send.html")
print("  " + "\n  ".join(ls.splitlines()[-4:]))

c.close()
