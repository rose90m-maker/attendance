#!/usr/bin/env python3
"""
CAPS Access MDB → MariaDB 동기화 프로그램
==========================================
C:\Caps\ACServer\access.mdb 의 tenter 테이블을
NAS MariaDB attendance.tenter 로 증분(incremental) 동기화.

사용법:
  python caps_sync.py                 # 콘솔 모드 (60초 주기 자동 반복)
  python caps_sync.py --once          # 1회 실행 후 종료
  python caps_sync.py --setup         # ★ 최초 1회: pip 설치 + bat 생성 + 시작프로그램 등록

설치 (Windows에서):
  1) caps_sync.py 를 C:\Caps\ACServer\ 에 복사
  2) 명령프롬프트(관리자)에서: python caps_sync.py --setup
  3) 끝! PC 부팅 시 자동 실행됨
"""

import os
import sys
import time
import socket
import shutil
import logging
import argparse
import signal
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────── 설정 ───────────────────────
MDB_PATH     = r"C:\Caps\ACServer\access.mdb"
MDB_PWD      = os.environ["CAPS_MDB_PWD"]
MDB_TABLE    = "tenter"

# === DB 연결 (메인 시스템과 동일한 .env 환경변수 공유) ===
# 미래에 별도 DB 분리 필요 시 CAPS_DB_* 키 추가
MARIA_HOST   = os.environ.get("DB_HOST", "127.0.0.1")
MARIA_PORT   = int(os.environ.get("DB_PORT", "3307"))
MARIA_USER   = os.environ.get("DB_USER", "root")
MARIA_PASS   = os.environ["DB_PASSWORD"]
MARIA_DB     = os.environ.get("DB_NAME", "attendance")
MARIA_CHARSET = "utf8mb4"

SYNC_INTERVAL_SEC  = 60        # 동기화 주기 (초)
RETRY_BASE_SEC     = 5         # 재시도 기본 대기 (초)
RETRY_MAX_SEC      = 300       # 재시도 최대 대기 (초)
BATCH_SIZE         = 5000      # INSERT 배치 크기

# ── 워치독 설정 ──
WATCHDOG_STALE_SEC = 300       # 5분 이상 헬스 갱신 없으면 "죽은 것"으로 판단
WATCHDOG_INTERVAL_MIN = 2      # 워치독 실행 주기 (분) — schtasks에 전달
WATCHDOG_TASK_NAME = "CAPS_Watchdog"

SCRIPT_DIR = Path(__file__).parent.resolve()
LOG_DIR    = SCRIPT_DIR / "logs"
LOG_FILE   = LOG_DIR / "caps_sync.log"
HEALTH_FILE = LOG_DIR / "last_success.txt"   # 헬스체크용

HOSTNAME = socket.gethostname()

# ─── tenter 컬럼 (id 제외, MDB→MariaDB 매핑) ───
TENTER_COLS = [
    "e_date", "e_time", "g_id", "e_id", "e_name", "e_idno",
    "e_group", "e_user", "e_mode", "e_type", "e_result",
    "e_etc", "e_picture", "e_uptime", "e_upmode", "e_card",
    "e_exyn", "e_maskst", "e_tempst", "e_index", "e_bodyheat",
]

# ─────────────────────── 로깅 ───────────────────────

def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(str(LOG_FILE), maxBytes=10*1024*1024,
                             backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)

    log = logging.getLogger("caps_sync")
    log.setLevel(logging.INFO)
    if not log.handlers:
        log.addHandler(fh)
        log.addHandler(ch)
    return log

log = setup_logging()

# ─────────────────────── DB 연결 ───────────────────────

def connect_maria(timeout=10):
    import pymysql
    return pymysql.connect(
        host=MARIA_HOST, port=MARIA_PORT,
        user=MARIA_USER, password=MARIA_PASS,
        database=MARIA_DB, charset=MARIA_CHARSET,
        connect_timeout=timeout,
        read_timeout=60, write_timeout=60,
        autocommit=False,
    )


def connect_mdb():
    """Access MDB 연결 — 잠금 충돌 방지를 위해 복사본 사용. ODBC 실패 시 1회 재시도."""
    import pyodbc
    for attempt in range(2):
        if not os.path.isfile(MDB_PATH):
            raise FileNotFoundError(f"MDB 파일 없음: {MDB_PATH}")

        tmp_dir = LOG_DIR / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_mdb = tmp_dir / "access_copy.mdb"
        try:
            shutil.copy2(MDB_PATH, tmp_mdb)
        except (PermissionError, OSError) as e:
            log.warning(f"MDB 복사 실패({e}), 원본 직접 접근")
            tmp_mdb = Path(MDB_PATH)

        conn_str = (
            r"Driver={Microsoft Access Driver (*.mdb, *.accdb)};"
            f"Dbq={tmp_mdb};"
            f"PWD={MDB_PWD};"
            r"ReadOnly=1;"
        )
        try:
            return pyodbc.connect(conn_str, timeout=30)
        except Exception as e:
            if attempt == 0:
                log.warning(f"ODBC 연결 실패 — 15초 후 재시도 ({e})")
                time.sleep(15)
            else:
                raise


def write_sync_log(conn, level, message):
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sync_log (log_time, host, level, message) "
                "VALUES (NOW(), %s, %s, %s)",
                (HOSTNAME, level, message),
            )
        conn.commit()
    except Exception as e:
        log.warning(f"sync_log 기록 실패: {e}")

# ─────────────────────── 핵심 동기화 ───────────────────────

def get_last_uptime(maria_conn):
    with maria_conn.cursor() as cur:
        cur.execute("SELECT IFNULL(MAX(e_uptime),'00000000000000') FROM tenter")
        row = cur.fetchone()
        return row[0] if row else "00000000000000"


def get_total_rows(maria_conn):
    with maria_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM tenter")
        return cur.fetchone()[0]


def get_date_range(maria_conn):
    with maria_conn.cursor() as cur:
        cur.execute("SELECT MIN(e_date), MAX(e_date) FROM tenter")
        row = cur.fetchone()
        return row[0] or "?", row[1] or "?"


def fetch_new_rows_from_mdb(mdb_conn, last_uptime):
    col_list = ", ".join(TENTER_COLS)
    sql = f"SELECT {col_list} FROM {MDB_TABLE} WHERE e_uptime > ? ORDER BY e_uptime"
    cur = mdb_conn.cursor()
    cur.execute(sql, (last_uptime,))
    rows = cur.fetchall()
    cur.close()
    return rows


def insert_rows(maria_conn, rows):
    """MariaDB에 배치 INSERT IGNORE (중복 방지)"""
    if not rows:
        return 0

    placeholders = ", ".join(["%s"] * len(TENTER_COLS))
    col_list = ", ".join(TENTER_COLS)
    sql = f"INSERT IGNORE INTO tenter ({col_list}) VALUES ({placeholders})"

    inserted = 0
    with maria_conn.cursor() as cur:
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            data = [tuple(r) for r in batch]
            cur.executemany(sql, data)
            maria_conn.commit()          # 배치 단위 커밋 → 부분 실패 방지
            inserted += len(batch)
    return inserted


def update_health():
    """마지막 성공 시각을 파일에 기록 (외부 모니터링용)"""
    try:
        HEALTH_FILE.write_text(datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               encoding="utf-8")
    except Exception:
        pass


def do_sync():
    """1회 동기화. 성공=True, 신규없음=None, 실패=예외"""
    import pymysql
    maria = connect_maria()
    try:
        last_uptime = get_last_uptime(maria)
        write_sync_log(maria, "INFO",
                       f"===== sync start (MDB={MDB_PATH}) =====")
        log.info(f"동기화 시작 (last_uptime={last_uptime})")

        mdb = connect_mdb()
        try:
            new_rows = fetch_new_rows_from_mdb(mdb, last_uptime)
        finally:
            mdb.close()

        if not new_rows:
            total = get_total_rows(maria)
            write_sync_log(maria, "SKIP",
                           f"tenter: no new data (last={last_uptime})")
            write_sync_log(maria, "INFO",
                           f"===== sync done: tenter {total} rows =====")
            log.info("신규 데이터 없음")
            update_health()
            return None

        max_date = max(r[0] for r in new_rows if r[0])
        count = insert_rows(maria, new_rows)

        new_last = get_last_uptime(maria)
        total = get_total_rows(maria)
        min_date, max_date_db = get_date_range(maria)

        write_sync_log(maria, "OK",
                       f"tenter: +{count} rows synced "
                       f"(last={last_uptime} -> max_date={max_date})")
        write_sync_log(maria, "INFO",
                       f"===== sync done: tenter {total} rows "
                       f"({min_date}~{max_date_db}) =====")
        log.info(f"동기화 완료: +{count}건 (last={last_uptime} → {new_last})")
        update_health()
        return True

    except Exception:
        try:
            write_sync_log(maria, "ERROR",
                           f"sync error: {sys.exc_info()[1]}")
        except Exception:
            pass
        raise
    finally:
        try:
            maria.close()
        except Exception:
            pass

# ─────────────────────── 메인 루프 ───────────────────────

_running = True

def _signal_handler(sig, frame):
    global _running
    log.info(f"종료 시그널 수신 ({sig})")
    _running = False

def run_loop():
    global _running
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    import pymysql, pyodbc  # noqa: 임포트 확인

    log.info(f"=== CAPS Sync 시작 (주기={SYNC_INTERVAL_SEC}초, "
             f"MDB={MDB_PATH}, DB={MARIA_HOST}:{MARIA_PORT}) ===")

    retry_wait = RETRY_BASE_SEC
    consecutive_errors = 0

    while _running:
        try:
            do_sync()
            consecutive_errors = 0
            retry_wait = RETRY_BASE_SEC
        except FileNotFoundError as e:
            consecutive_errors += 1
            log.error(f"MDB 파일 없음: {e} — {retry_wait}초 후 재시도 (연속 {consecutive_errors}회)")
            if consecutive_errors >= 2:
                try:
                    conn = connect_maria(timeout=5)
                    write_sync_log(conn, "ERROR",
                                   f"sync error ({consecutive_errors}회 연속): {e}")
                    conn.close()
                except Exception:
                    pass
            retry_wait = min(retry_wait * 2, RETRY_MAX_SEC)
        except Exception as e:
            consecutive_errors += 1
            ename = type(e).__name__
            log.error(f"{ename}: {e} — {retry_wait}초 후 재시도 (연속 {consecutive_errors}회)")
            if consecutive_errors >= 2:
                try:
                    conn = connect_maria(timeout=5)
                    write_sync_log(conn, "ERROR",
                                   f"sync error ({consecutive_errors}회 연속): {e}")
                    conn.close()
                except Exception:
                    pass
            retry_wait = min(retry_wait * 2, RETRY_MAX_SEC)

        wait = retry_wait if retry_wait > RETRY_BASE_SEC else SYNC_INTERVAL_SEC
        for _ in range(wait):
            if not _running:
                break
            time.sleep(1)

    log.info("=== CAPS Sync 종료 ===")


def run_once():
    try:
        result = do_sync()
        if result is True:
            log.info("1회 동기화 완료 (신규 데이터 동기)")
        else:
            log.info("1회 동기화 완료 (신규 없음)")
        sys.exit(0)
    except Exception as e:
        log.error(f"동기화 실패: {e}", exc_info=True)
        sys.exit(1)

# ─────────────────── 워치독 (자동 재시작) ───────────────────

def _health_age_sec():
    """마지막 성공으로부터 경과 시간(초). 파일 없거나 파싱 실패 시 None."""
    try:
        if not HEALTH_FILE.exists():
            return None
        text = HEALTH_FILE.read_text(encoding="utf-8").strip()
        last = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - last).total_seconds()
    except Exception:
        return None


def _kill_caps_sync_processes():
    """현재 실행 중인 caps_sync.py 파이썬 프로세스 종료 (자기 자신 제외)"""
    killed = 0
    my_pid = os.getpid()
    try:
        # WMIC로 커맨드라인 조회 후 caps_sync.py 포함 PID 추출
        out = subprocess.check_output(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "ProcessId,CommandLine", "/format:list"],
            stderr=subprocess.DEVNULL, timeout=15,
        ).decode("utf-8", errors="ignore")
    except Exception as e:
        log.warning(f"WMIC 조회 실패: {e}")
        return 0

    cmd = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("CommandLine="):
            cmd = line[len("CommandLine="):]
        elif line.startswith("ProcessId="):
            pid_str = line[len("ProcessId="):]
            try:
                pid = int(pid_str)
            except ValueError:
                continue
            if pid == my_pid:
                cmd = None
                continue
            if cmd and "caps_sync.py" in cmd and "--watchdog" not in cmd:
                try:
                    subprocess.check_call(
                        ["taskkill", "/PID", str(pid), "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    killed += 1
                    log.info(f"프로세스 종료 PID={pid}")
                except Exception as e:
                    log.warning(f"프로세스 종료 실패 PID={pid}: {e}")
            cmd = None
    return killed


def _start_caps_sync():
    """CAPS동기화.bat을 백그라운드로 실행"""
    bat_path = SCRIPT_DIR / "CAPS동기화.bat"
    if not bat_path.exists():
        # bat 없으면 python 직접 실행
        cmd = [sys.executable, str(Path(__file__).resolve())]
    else:
        cmd = ["cmd", "/c", "start", "", str(bat_path)]
    try:
        subprocess.Popen(cmd, cwd=str(SCRIPT_DIR),
                         creationflags=subprocess.CREATE_NEW_CONSOLE
                         if os.name == "nt" else 0)
        log.info(f"caps_sync 재시작: {' '.join(str(c) for c in cmd)}")
        return True
    except Exception as e:
        log.error(f"caps_sync 시작 실패: {e}")
        return False


def watchdog_once():
    """
    1회 워치독 체크.
    - 마지막 성공이 WATCHDOG_STALE_SEC 이상 지났으면 재시작
    - 주말/공휴일 또는 업무시간 밖이면 관대하게 3배까지 봐줌
    """
    age = _health_age_sec()
    if age is None:
        log.info("헬스 파일 없음 → caps_sync 시작")
        _start_caps_sync()
        return "started"

    # 업무시간(평일 06:00~22:00) 밖이면 임계치 3배 적용
    now = datetime.now()
    is_work_hours = (now.weekday() < 5 and 6 <= now.hour < 22)
    threshold = WATCHDOG_STALE_SEC if is_work_hours else WATCHDOG_STALE_SEC * 3

    if age > threshold:
        log.warning(f"헬스 낡음: {age:.0f}초 경과 (임계치 {threshold}초) → 재시작")
        killed = _kill_caps_sync_processes()
        time.sleep(2)
        started = _start_caps_sync()
        # MariaDB sync_log에도 기록
        try:
            conn = connect_maria(timeout=5)
            write_sync_log(conn, "WATCHDOG",
                           f"restarted: health stale {age:.0f}s "
                           f"(threshold={threshold}s, killed={killed}, started={started})")
            conn.close()
        except Exception:
            pass
        return "restarted"
    else:
        log.info(f"정상: 헬스 {age:.0f}초 전 (임계치 {threshold}초)")
        return "ok"


def setup_watchdog():
    """Windows 작업 스케줄러에 워치독 등록 (schtasks)"""
    print("\n[워치독 등록] Windows 작업 스케줄러 등록 중...")
    script_path = Path(__file__).resolve()
    python_exe = Path(sys.executable).resolve()

    # 기존 태스크 삭제 (있으면)
    subprocess.run(
        ["schtasks", "/Delete", "/TN", WATCHDOG_TASK_NAME, "/F"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    # 새로 등록: 2분마다 SYSTEM 계정으로 워치독 실행
    tr = f'"{python_exe}" "{script_path}" --watchdog'
    cmd = [
        "schtasks", "/Create",
        "/TN", WATCHDOG_TASK_NAME,
        "/TR", tr,
        "/SC", "MINUTE",
        "/MO", str(WATCHDOG_INTERVAL_MIN),
        "/RU", "SYSTEM",
        "/RL", "HIGHEST",
        "/F",
    ]
    try:
        subprocess.check_call(cmd)
        print(f"  → 워치독 등록 완료 ({WATCHDOG_INTERVAL_MIN}분 주기, SYSTEM 권한)")
        print(f"     태스크명: {WATCHDOG_TASK_NAME}")
        print(f"     확인: schtasks /Query /TN {WATCHDOG_TASK_NAME} /V")
    except subprocess.CalledProcessError as e:
        print(f"  → 워치독 등록 실패: {e}")
        print("     관리자 권한으로 실행하세요")


# ─────────────────── --setup: 원클릭 설치 ───────────────────

def do_setup():
    """pip 패키지 설치 + bat 생성 + 시작프로그램 등록"""
    print("=" * 50)
    print("  CAPS 동기화 프로그램 설치")
    print("=" * 50)

    # 1) pip 패키지 설치
    print("\n[1/3] 필요한 패키지 설치 중...")
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "pyodbc", "pymysql", "--quiet"])
    print("  → pyodbc, pymysql 설치 완료")

    # 2) bat 파일 생성
    print("\n[2/3] 실행 파일 생성 중...")
    script_path = Path(__file__).resolve()
    bat_path = script_path.parent / "CAPS동기화.bat"

    python_exe = Path(sys.executable).resolve()
    bat_content = f'''@echo off
chcp 65001 >nul
title CAPS 출입 동기화
echo ============================================
echo   CAPS 출입 동기화 프로그램
echo   종료: 이 창을 닫으세요 (X)
echo ============================================
echo.
cd /d "{script_path.parent}"
"{python_exe}" "{script_path}"
pause
'''
    bat_path.write_text(bat_content, encoding="utf-8")
    print(f"  → {bat_path}")

    # 3) 시작프로그램 폴더에 바로가기 복사
    print("\n[3/3] 시작프로그램 등록 중...")
    startup_dir = Path(os.environ.get("APPDATA", "")) / \
        r"Microsoft\Windows\Start Menu\Programs\Startup"

    if startup_dir.exists():
        # VBScript로 바로가기(.lnk) 생성
        lnk_path = startup_dir / "CAPS동기화.lnk"
        vbs = tempfile.NamedTemporaryFile(mode="w", suffix=".vbs",
                                          delete=False, encoding="utf-8")
        vbs.write(f'''Set sh = CreateObject("WScript.Shell")
Set lnk = sh.CreateShortcut("{lnk_path}")
lnk.TargetPath = "{bat_path}"
lnk.WorkingDirectory = "{script_path.parent}"
lnk.Description = "CAPS 출입 동기화"
lnk.WindowStyle = 7
lnk.Save
''')
        vbs.close()
        try:
            subprocess.check_call(["cscript", "//nologo", vbs.name])
            print(f"  → 시작프로그램 등록 완료")
            print(f"     {lnk_path}")
        except Exception as e:
            print(f"  → 바로가기 생성 실패: {e}")
            print(f"     수동으로 '{bat_path}'를 시작프로그램 폴더에 넣어주세요")
        finally:
            os.unlink(vbs.name)
    else:
        print(f"  → 시작프로그램 폴더를 찾을 수 없어 수동 등록 필요")
        print(f"     bat 파일: {bat_path}")

    # 4) 로그 디렉토리 생성
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 5) 워치독 등록 (자동 재시작)
    setup_watchdog()

    print("\n" + "=" * 50)
    print("  설치 완료!")
    print(f"  바탕화면의 'CAPS동기화.bat' 더블클릭으로 실행")
    print(f"  PC 부팅 시 자동 실행")
    print(f"  워치독: {WATCHDOG_INTERVAL_MIN}분마다 자동 상태 점검/재시작")
    print("=" * 50)

    # 바로 실행할지 물어보기
    try:
        answer = input("\n지금 바로 실행할까요? (Y/n): ").strip().lower()
        if answer in ("", "y", "yes"):
            run_loop()
    except (EOFError, KeyboardInterrupt):
        pass

# ─────────────────────── CLI ───────────────────────

def main():
    parser = argparse.ArgumentParser(description="CAPS MDB → MariaDB 동기화")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true",
                       help="1회 실행 후 종료")
    group.add_argument("--setup", action="store_true",
                       help="★ 최초 설치 (pip + bat + 시작프로그램 + 워치독 등록)")
    group.add_argument("--watchdog", action="store_true",
                       help="워치독 1회 실행 (헬스체크 후 필요 시 재시작)")
    group.add_argument("--setup-watchdog", action="store_true",
                       help="워치독만 작업 스케줄러에 등록")
    args = parser.parse_args()

    if args.setup:
        do_setup()
    elif args.setup_watchdog:
        setup_watchdog()
    elif args.watchdog:
        watchdog_once()
    elif args.once:
        run_once()
    else:
        run_loop()


if __name__ == "__main__":
    main()
