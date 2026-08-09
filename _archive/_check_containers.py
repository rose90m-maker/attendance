#!/usr/bin/env python3
"""_check_containers.py — 컨테이너 상태 읽기 전용 진단 (일회용 디버그 스크립트)

재빌드 실패 원인 파악용. .env 의 NAS_* 자격증명을 그대로 재사용한다
(deploy_and_restart.py 와 동일한 접속 방식 — 비밀번호 직접 입력 불필요).

아무것도 바꾸지 않는다. docker ps / inspect / logs / images 만 읽는다.

사용:  python3 _check_containers.py
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

# ── .env 확인 ────────────────────────────────────────────────
need = ["NAS_HOST", "NAS_USER", "NAS_PASS", "NAS_SUDO"]
missing = [k for k in need if not os.environ.get(k)]
if missing:
    sys.exit(f".env 에 다음 키가 없습니다: {', '.join(missing)}")

NAS_HOST = os.environ["NAS_HOST"]
NAS_USER = os.environ["NAS_USER"]
NAS_PASS = os.environ["NAS_PASS"]
NAS_SUDO = os.environ["NAS_SUDO"]

MAIN, TBM = "attendance-app", "attendance-tbm"

# ── 출력 마스킹 ──────────────────────────────────────────────
# 로그/inspect 결과에 자격증명이 섞여 나올 수 있으므로 화면에 찍기 전에 가린다.
_SECRETS = [v for v in (NAS_PASS, NAS_SUDO, os.environ.get("DB_PASSWORD", "")) if v]
_PATTERNS = [
    re.compile(r'((?:password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*)'
               r'(["\']?)([^\s"\',}]+)(\2)', re.I),
]


def mask(text: str) -> str:
    for s in _SECRETS:
        text = text.replace(s, "***")
    for p in _PATTERNS:
        text = p.sub(lambda m: f"{m.group(1)}{m.group(2)}***{m.group(4)}", text)
    return text


# 터미널 스크롤백에 잘리지 않도록 파일에도 같이 남긴다.
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_diag.txt")
_out = open(OUT_PATH, "w", encoding="utf-8")


def say(*args):
    text = " ".join(str(a) for a in args)
    print(text)
    _out.write(text + "\n")
    _out.flush()


def head(title: str):
    say(f"\n{'=' * 68}\n  {title}\n{'=' * 68}")


# ── 접속 (deploy_and_restart.py 와 동일 순서) ────────────────
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
_key = os.path.expanduser("~/.ssh/id_rsa")
try:
    if os.path.exists(_key):
        try:
            c.connect(NAS_HOST, port=22, username=NAS_USER, key_filename=_key, timeout=15)
        except Exception:
            c.connect(NAS_HOST, port=22, username=NAS_USER, password=NAS_PASS, timeout=15)
    else:
        c.connect(NAS_HOST, port=22, username=NAS_USER, password=NAS_PASS, timeout=15)
except Exception as e:
    sys.exit(f"NAS 접속 실패: {type(e).__name__}: {e}\n"
             f"  → .env 의 NAS_HOST / NAS_USER / NAS_PASS 를 확인하세요.")

say(f"접속 성공: {NAS_USER}@{NAS_HOST}")


def sudo_nas(cmd: str) -> str:
    """sudo 로 NAS 명령 실행. 출력 앞의 'Password: ' 프롬프트를 제거한다.

    명령을 base64 로 실어 보낸다. docker --format / inspect -f 의 작은따옴표가
    sh -c '...' 의 따옴표를 조기에 닫아버리는 문제를 원천 차단하기 위함이다.
    """
    payload = base64.b64encode(
        f"PATH=/usr/local/bin:$PATH\n{cmd}\n".encode("utf-8")
    ).decode("ascii")
    full = f"echo '{NAS_SUDO}' | sudo -S sh -c \"echo {payload} | base64 -d | sh\" 2>&1"
    _, out, _ = c.exec_command(full, timeout=60)
    text = out.read().decode("utf-8", "replace")
    return re.sub(r'^Password:\s*', '', text)


# ── 1. 컨테이너 목록 ─────────────────────────────────────────
head("1. 컨테이너 목록 (docker ps -a)")
say(mask(sudo_nas(
    "docker ps -a --format '{{.Names}} | {{.Status}} | {{.Image}} | {{.Ports}}'"
)).strip() or "(출력 없음)")

# ── 2. 이미지 목록 ───────────────────────────────────────────
head("2. attendance 이미지 (docker images)")
say(mask(sudo_nas(
    "docker images --format '{{.Repository}}:{{.Tag}} | {{.ID}} | {{.CreatedSince}} | {{.Size}}' "
    "| grep -i attendance"
)).strip() or "(attendance 이미지 없음)")

# ── 3. 컨테이너별 상세 ───────────────────────────────────────
# 5월 19일 사고 재발 점검: CMD 가 의도한 스크립트를 가리키는지, 포트 매핑이 맞는지.
for name in (MAIN, TBM):
    head(f"3. {name} — 실행 설정")
    fmt = ('Status={{.State.Status}}  Restarts={{.RestartCount}}  '
           'ExitCode={{.State.ExitCode}}  OOMKilled={{.State.OOMKilled}}\n'
           'StartedAt={{.State.StartedAt}}  FinishedAt={{.State.FinishedAt}}\n'
           'Error={{.State.Error}}\n'
           'RestartPolicy={{.HostConfig.RestartPolicy.Name}}'
           '  MaxRetry={{.HostConfig.RestartPolicy.MaximumRetryCount}}\n'
           'Health={{if .State.Health}}{{.State.Health.Status}}'
           ' (fail {{.State.Health.FailingStreak}}){{else}}없음{{end}}\n'
           'MemLimit={{.HostConfig.Memory}}\n'
           'Cmd={{.Config.Cmd}}\nEntrypoint={{.Config.Entrypoint}}\n'
           'Image={{.Config.Image}}\nPorts={{.HostConfig.PortBindings}}')
    say(mask(sudo_nas(f"docker inspect -f '{fmt}' {name}")).strip() or "(컨테이너 없음)")

    # 컨테이너 안에서 실제로 무엇이 어느 포트를 잡고 있는지
    say("\n-- 컨테이너 내부 프로세스 --")
    say(mask(sudo_nas(f"docker top {name} 2>&1 | head -6")).strip() or "(미실행)")

# ── 4. 환경변수 — 값이 아니라 '길이'만 ───────────────────────
# 5월 19일 사고: --env-file 로 박힌 옛 DB_PASSWORD 가 .env 를 덮어써서 1045 발생.
head("4. DB_PASSWORD 길이 비교 (값은 출력하지 않음)")
for name in (MAIN, TBM):
    out = sudo_nas(
        f"docker inspect -f '{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}' {name} "
        f"| grep '^DB_PASSWORD=' | head -1"
    ).strip()
    if out.startswith("DB_PASSWORD="):
        say(f"  {name:16} 컨테이너 env DB_PASSWORD 길이 = {len(out.split('=', 1)[1])}")
    elif not out:
        say(f"  {name:16} 컨테이너 env 에 DB_PASSWORD 키 없음 (/app/.env 로 주입되는 구조)")
    else:
        say(f"  {name:16} 조회 실패 → {out[:120]}")

# 컨테이너 안 /app/.env 가 실제로 앱이 읽는 값이므로 그 길이도 본다.
for name in (MAIN, TBM):
    out = sudo_nas(
        f"docker exec {name} sh -c "
        f"\"grep '^DB_PASSWORD=' /app/.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\\r\\n' | wc -c\""
    ).strip()
    say(f"  {name:16} /app/.env 의 DB_PASSWORD 길이 = {out or '(읽기 실패)'}")

local_db = os.environ.get("DB_PASSWORD", "")
say(f"  {'맥 .env':16} DB_PASSWORD 길이 = {len(local_db) if local_db else '(없음)'}")
say("  → 길이가 다르면 5월 19일과 같은 1045 인증 실패 원인입니다.")

# ── 5. 로그 ──────────────────────────────────────────────────
for name in (MAIN, TBM):
    head(f"5. {name} — 최근 로그 60줄")
    say(mask(sudo_nas(f"docker logs --tail 60 {name}")).strip() or "(로그 없음)")

# ── 6. 포트 점유 ─────────────────────────────────────────────
head("6. NAS 포트 5050 / 5051 점유 상태")
say(mask(sudo_nas(
    "docker port " + MAIN + " 2>&1; docker port " + TBM + " 2>&1; "
    "(ss -tln 2>/dev/null || netstat -tln 2>/dev/null) | grep -E ':(5050|5051)' "
    "|| echo '(ss/netstat 사용 불가)'"
)).strip() or "(출력 없음)")

# ── 7. 메모리 사용량 ─────────────────────────────────────────
# 20~30분 주기 재시작은 OOM kill 가능성이 있어 현재 사용량을 본다.
head("7. 메모리 · CPU 사용량 (docker stats)")
say(mask(sudo_nas(
    "docker stats --no-stream --format "
    "'{{.Name}} | CPU {{.CPUPerc}} | MEM {{.MemUsage}} ({{.MemPerc}})'"
)).strip() or "(출력 없음)")

# ── 8. 헬스체크 최근 결과 ────────────────────────────────────
head("8. 헬스체크 최근 3회 (설정된 경우)")
for name in (MAIN, TBM):
    out = sudo_nas(
        f"docker inspect -f '{{{{if .State.Health}}}}"
        f"{{{{range .State.Health.Log}}}}[{{{{.ExitCode}}}}] {{{{.Output}}}}{{{{end}}}}"
        f"{{{{else}}}}없음{{{{end}}}}' {name} | tail -20"
    ).strip()
    say(f"-- {name} --")
    say(mask(out) or "(없음)")

# ── 9. NAS 시스템 로그의 OOM 흔적 ───────────────────────────
head("9. 커널 OOM kill 흔적")
say(mask(sudo_nas(
    "dmesg 2>/dev/null | grep -iE 'out of memory|oom-kill|killed process' | tail -10 "
    "|| echo '(dmesg 접근 불가 또는 흔적 없음)'"
)).strip() or "(흔적 없음)")

c.close()
_out.close()
print(f"\n>>> 전체 결과가 {OUT_PATH} 에 저장되었습니다.")
print("진단 완료 — 위 출력을 그대로 붙여주세요 (자격증명은 마스킹되어 있습니다).")
