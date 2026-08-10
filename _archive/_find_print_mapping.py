#!/usr/bin/env python3
"""_find_print_mapping.py — ERP 의 '항목 → 서식 칸' 매핑 찾기 (읽기 전용)

wht_calc.py 는 발급본 하나(지창구 2025)를 보고 역공학한 계산 엔진이라,
안 겪어본 공제 조합에서 ERP 와 다른 값을 낸다 — 189명 중 186명에서
결정세액·산출세액·근로소득세액공제 등이 어긋난다 (2026-08-10 확인).

ERP 는 계산 결과를 _TWPRAdjTotResultDtl 에 이미 갖고 있다.
그 값을 그대로 쓰면 구조적으로 일치한다. 필요한 건 매핑 하나다:

    AdjItemSeq  →  서식 토큰(Data6_AmtNN)

ERP 에 _TWPRAdjTotPrintMapping / _TWPRAdjTotPrintMappingDtl 이 있으니
거기에 답이 있을 가능성이 높다. 없으면 지창구 데이터로 역산한다.

ERP 는 읽기만 한다.

사용:  python3 _archive/_find_print_mapping.py [--yy 2025]
"""
import argparse
import os
import sys

HERE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import wht_receipt as W

ap = argparse.ArgumentParser()
ap.add_argument("--yy", default="2025")
ap.add_argument("--name", default="지창구")
args = ap.parse_args()


def head(t):
    print(f"\n{'=' * 74}\n  {t}\n{'=' * 74}")


conn = W._conn()
cur = conn.cursor()

# ── 1. 매핑 테이블 구조 ──────────────────────────────────────
for tbl in ("_TWPRAdjTotPrintMapping", "_TWPRAdjTotPrintMappingDtl",
            "_TWPRAdjTotPrint", "_TWPRAdjTotRptSetting"):
    head(f"{tbl}")
    try:
        cur.execute("""SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
                       WHERE TABLE_NAME=%s ORDER BY ORDINAL_POSITION""", (tbl,))
        cols = cur.fetchall()
        if not cols:
            print("   (테이블 없음)")
            continue
        print("   컬럼: " + ", ".join(f"{c}({d})" for c, d in cols))
        cur.execute(f"SELECT COUNT(*) FROM [{tbl}]")
        print(f"   행수: {cur.fetchone()[0]:,}")
        cur.execute(f"SELECT TOP 12 * FROM [{tbl}]")
        names = [d[0] for d in cur.description]
        print("   " + " | ".join(names))
        for r in cur.fetchall():
            print("   " + " | ".join(
                str(x).strip()[:26] if x is not None else "" for x in r))
    except Exception as e:
        print(f"   조회 실패: {str(e)[:120]}")

# ── 2. 토큰명이 들어 있는 컬럼이 있는지 ──────────────────────
head("서식 토큰명(Data6_Amt…)이 저장된 곳 찾기")
cur.execute("""SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
               WHERE DATA_TYPE IN ('nvarchar','varchar','nchar','char')
                 AND TABLE_NAME LIKE '%AdjTot%'""")
cands = cur.fetchall()
found = 0
for tbl, col in cands:
    try:
        cur.execute(f"SELECT COUNT(*) FROM [{tbl}] WHERE [{col}] LIKE %s",
                    ('%Data6_Amt%',))
        n = cur.fetchone()[0]
    except Exception:
        continue
    if n:
        found += 1
        print(f"  ✅ {tbl}.{col}  ({n}행)")
        cur.execute(f"SELECT TOP 8 [{col}] FROM [{tbl}] WHERE [{col}] LIKE %s",
                    ('%Data6_Amt%',))
        for r in cur.fetchall():
            print(f"       {str(r[0])[:100]}")
if not found:
    print("  ❌ 토큰명이 DB 에 저장돼 있지 않습니다.")
    print("     → 매핑은 지창구 데이터로 역산해야 합니다 (아래 3번).")

# ── 3. 지창구 데이터로 역산 준비 ─────────────────────────────
head(f"{args.name} — ERP 항목값 (역산용 기초자료)")
cur.execute("""SELECT EmpSeq FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
r = cur.fetchone()
if not r:
    print("   대상 없음")
else:
    emp_seq = r[0]
    cur.execute("SELECT AdjItemSeq, AdjItemName FROM _TWPRAdjTotItem WHERE YY=%s",
                (args.yy,))
    item = {x[0]: (x[1] or "").strip() for x in cur.fetchall()}
    cur.execute("""SELECT AdjItemSeq, Amt, OrgAmt FROM _TWPRAdjTotResultDtl
                   WHERE YY=%s AND EmpSeq=%s ORDER BY AdjItemSeq""",
                (args.yy, emp_seq))
    rows = [x for x in cur.fetchall()
            if (x[1] and float(x[1])) or (x[2] and float(x[2]))]
    print(f"   0 아닌 항목 {len(rows)}개\n")
    print(f"   {'seq':>6} {'Amt':>15} {'OrgAmt':>15}  항목명")
    for seq, amt, org in rows:
        print(f"   {seq:>6} {int(float(amt or 0)):>15,} "
              f"{int(float(org or 0)):>15,}  {item.get(seq,'')[:40]}")

conn.close()
print("\n1·2번에 매핑이 있으면 그걸 쓰고, 없으면 3번 표로 역산합니다.")
