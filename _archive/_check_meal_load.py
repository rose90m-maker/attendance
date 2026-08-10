#!/usr/bin/env python3
"""_check_meal_load.py — 관제 대시보드 식수현황 적재 상태 확인 (읽기 전용)

관제 대시보드(/control_center → /api/cc_overview)는 식수관리 화면의 `meal_count` 가
아니라 **`meal_order_monthly` / `meal_order_daily`** 를 본다. 이 둘은
`meal_order_import.py` 가 SMB 공유의 엑셀을 읽어 채우는데, **자동 실행이 아니다**
(cron·LaunchAgent 없음). 그래서 안 돌리면 대시보드가 비어 보인다.

이 스크립트는 DB 와 원본 엑셀을 대조해 "이번 달이 적재됐는지"를 판정한다.
SELECT 만 하며 아무것도 쓰지 않는다.

사용:  python3 _archive/_check_meal_load.py
"""
import os
import sys
from datetime import date

try:
    import pymysql
except ImportError:
    sys.exit("pymysql 이 없습니다.  pip install pymysql  후 다시 실행하세요.")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if not os.environ.get("DB_PASSWORD"):
    sys.exit(".env 에 DB_PASSWORD 가 없습니다.")

SRC_DIR = os.environ.get("MEAL_DIR", "/Volumes/taein_hq/이민영/식수현황")

today = date.today()
Y, M = today.year, today.month

try:
    conn = pymysql.connect(
        host=os.environ.get("DB_HOST", "192.168.100.11"),
        port=int(os.environ.get("DB_PORT", 3307)),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ["DB_PASSWORD"],
        database="attendance", charset="utf8mb4",
    )
except Exception as e:
    sys.exit(f"DB 접속 실패: {type(e).__name__}: {e}\n"
             f"  → NAS 3307 포트와 .env 의 DB_* 를 확인하세요.")

cur = conn.cursor()


def head(t):
    print(f"\n{'=' * 62}\n  {t}\n{'=' * 62}")


print(f"기준일: {today}  (확인 대상: {Y}년 {M}월)")

# ── 1. 월별 집계 ─────────────────────────────────────────────
head("1. meal_order_monthly — 월별 발주식수")
cur.execute("""SELECT `year`, `month`, breakfast, lunch, dinner, night, days
               FROM meal_order_monthly
               WHERE `year`=%s ORDER BY `month`""", (Y,))
rows = cur.fetchall()
if rows:
    print(f"  {'월':>4} {'조식':>7} {'중식':>7} {'석식':>7} {'야식':>7} {'일수':>5}")
    for y, m, b, l, d, n, dy in rows:
        mark = "  ← 이번 달" if m == M else ""
        print(f"  {m:>3}월 {b or 0:>7,} {l or 0:>7,} {d or 0:>7,} "
              f"{n or 0:>7,} {dy or 0:>5}{mark}")
else:
    print(f"  {Y}년 데이터가 하나도 없습니다.")

this_month = [r for r in rows if r[1] == M]
has_monthly = bool(this_month)

# ── 2. 일별 상세 ─────────────────────────────────────────────
head("2. meal_order_daily — 이번 달 일별")
first = date(Y, M, 1)
nxt = date(Y + 1, 1, 1) if M == 12 else date(Y, M + 1, 1)
cur.execute("""SELECT order_date, breakfast, lunch, dinner, night
               FROM meal_order_daily
               WHERE order_date >= %s AND order_date < %s
               ORDER BY order_date""", (first, nxt))
daily = cur.fetchall()

if daily:
    last_day = daily[-1][0]
    print(f"  적재된 날짜: {len(daily)}일치  ({daily[0][0]} ~ {last_day})")
    print(f"  {'일':>3} {'요일':>4} {'조식':>6} {'중식':>6} {'석식':>6} {'야식':>6}")
    DOWK = ["월", "화", "수", "목", "금", "토", "일"]
    for od, b, l, d, n in daily[-10:]:
        print(f"  {od.day:>3} {DOWK[od.weekday()]:>4} {b or 0:>6} "
              f"{l or 0:>6} {d or 0:>6} {n or 0:>6}")
    if len(daily) > 10:
        print(f"  (마지막 10일만 표시 — 전체 {len(daily)}일)")
    gap = (today - last_day).days
else:
    last_day = None
    gap = None
    print("  이번 달 일별 데이터가 없습니다.")

# ── 3. 원본 엑셀 ─────────────────────────────────────────────
head("3. 원본 엑셀 (SMB 공유)")
src_name = f"식수현황_{Y}_{M}.xlsx"
if not os.path.isdir(SRC_DIR):
    print(f"  ⚠️  공유 폴더에 접근할 수 없습니다: {SRC_DIR}")
    print("      Finder 에서 smb://192.168.100.3/taein_hq 를 마운트해야 합니다.")
    src_exists = None
else:
    import unicodedata
    names = [unicodedata.normalize("NFC", n) for n in os.listdir(SRC_DIR)]
    cand = [n for n in names if n.startswith(f"식수현황_{Y}_{M}")]
    src_exists = bool(cand)
    if cand:
        p = os.path.join(SRC_DIR, cand[0])
        import time
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))
        print(f"  ✅ {cand[0]}  (수정: {mtime})")
    else:
        print(f"  ❌ {src_name} 가 없습니다.")
        print(f"      폴더에 있는 최근 파일: "
              f"{', '.join(sorted(n for n in names if n.startswith('식수현황_'))[-3:])}")

# ── 판정 ─────────────────────────────────────────────────────
head("판정")
if has_monthly and daily:
    print(f"  ✅ {Y}년 {M}월 적재됨 — 일별 {len(daily)}일치, 최종 {last_day}")
    if gap is not None and gap > 3:
        print(f"  ⚠️  다만 최종 적재일이 {gap}일 전입니다. 그 뒤 데이터는 빠져 있습니다.")
        print("      원본 엑셀이 갱신됐다면 다시 돌려야 합니다:")
        print(f"      python3 meal_order_import.py {Y}_{M}")
elif has_monthly and not daily:
    print("  ⚠️  월별 집계는 있는데 일별이 없습니다. 대시보드의 '이번 달 일별' 그래프가 빕니다.")
    print(f"      python3 meal_order_import.py {Y}_{M}")
else:
    print(f"  ❌ {Y}년 {M}월 데이터가 적재되지 않았습니다.")
    if src_exists:
        print("      원본 엑셀은 있습니다. 임포트만 돌리면 됩니다:")
        print(f"      python3 meal_order_import.py {Y}_{M}")
    elif src_exists is False:
        print(f"      원본 엑셀 {src_name} 자체가 없습니다. 담당자에게 확인이 필요합니다.")
    else:
        print("      SMB 공유를 마운트한 뒤 다시 확인하세요.")

print("\n참고: meal_order_import.py 는 자동 실행되지 않습니다 (cron·LaunchAgent 없음).")
print("      매월 수동으로 돌려야 관제 대시보드에 반영됩니다.")

conn.close()
