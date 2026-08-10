#!/usr/bin/env python3
"""_dump_depenlist.py — _TWPRAdjTotEmpDepenList 구조·데이터 확인 (읽기 전용)

3쪽 「소득공제 명세서」 금액이 이 테이블에 있고, 컬럼명이 서식 토큰명
(Data7_<컬럼명>)과 1:1 로 대응하는 것으로 보인다. 그 가설을 확정한다.

  · 테이블 컬럼 전체
  · 지창구 2025 실제 행 (세로로 출력 — 컬럼이 많아서)
  · Page3 토큰 ↔ 컬럼 자동 대조 (일치/토큰만있음/컬럼만있음)

SELECT 전용.

사용:  python3 _archive/_dump_depenlist.py [--name 지창구] [--yy 2025]
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import wht_receipt as W

ap = argparse.ArgumentParser()
ap.add_argument("--name", default="지창구")
ap.add_argument("--yy", default="2025")
ap.add_argument("--table", default="_TWPRAdjTotEmpDepenList")
args = ap.parse_args()

TBL = args.table


def head(t):
    print(f"\n{'=' * 70}\n  {t}\n{'=' * 70}")


conn = W._conn()
cur = conn.cursor()

cur.execute("""SELECT EmpSeq, EmpID FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
row = cur.fetchone()
if not row:
    sys.exit(f"{args.yy} 귀속에 '{args.name}' 없음")
emp_seq = row[0]
print(f"대상: {args.name} · EmpSeq {emp_seq} · {args.yy} 귀속 · 테이블 {TBL}")

# ── 컬럼 ─────────────────────────────────────────────────────
cur.execute("""SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
               WHERE TABLE_NAME=%s ORDER BY ORDINAL_POSITION""", (TBL,))
cols = cur.fetchall()
colnames = [c for c, _ in cols]
head(f"1. 컬럼 {len(cols)}개")
for i in range(0, len(cols), 3):
    print("   " + "  ".join(f"{c:<26}({d})" for c, d in cols[i:i + 3]))

# ── 데이터 ───────────────────────────────────────────────────
head(f"2. {args.name} {args.yy} 행 — 세로 출력")
keys = [c for c in colnames if c in ("YY", "EmpSeq")]
where = " AND ".join(f"{k}=%s" for k in keys)
params = tuple(args.yy if k == "YY" else emp_seq for k in keys)
cur.execute(f"SELECT * FROM [{TBL}]" + (f" WHERE {where}" if keys else ""), params)
names = [d[0] for d in cur.description]
rows = cur.fetchall()
print(f"   {len(rows)}행\n")

if rows:
    # 값이 있는 컬럼만 보여준다 (전부 0/NULL 이면 생략)
    shown = 0
    for i, cn in enumerate(names):
        vals = [r[i] for r in rows]
        if all(v is None or str(v).strip() in ("", "0", "0.00000") for v in vals):
            continue
        shown += 1
        cells = []
        for v in vals:
            s = str(v).strip()
            if re.fullmatch(r"-?\d+\.\d+", s):
                s = f"{float(s):,.0f}"
            elif re.fullmatch(r"-?\d+", s):
                s = f"{int(s):,}"
            cells.append(f"{s:>16}")
        print(f"   {cn:<26}" + "".join(cells))
    print(f"\n   (값이 있는 컬럼 {shown}개만 표시 · 나머지는 0/NULL)")

# ── 토큰 ↔ 컬럼 대조 ─────────────────────────────────────────
head("3. Page3 토큰 ↔ 컬럼 자동 대조")
cur.execute("SELECT Page3 FROM _TWPRAdjTotAbrIncomeHTML WHERE YY=%s", (args.yy,))
r = cur.fetchone()
if not r or not r[0]:
    print("   Page3 서식 없음")
else:
    toks = sorted(set(re.findall(r"Y[Ll][Ww]#_Data7_([A-Za-z0-9_]+)", r[0])))
    colset = set(colnames)
    # Sum 접두사는 합계행 — 원본 컬럼으로 환원해 대조
    def base(t):
        for p in ("SumDisa", "SUM", "Sum"):
            if t.startswith(p):
                return t[len(p):]
        return t

    matched, only_tok = [], []
    for t in toks:
        b = base(t)
        if t in colset:
            matched.append((t, t))
        elif b in colset:
            matched.append((t, b))
        else:
            only_tok.append(t)

    print(f"   토큰 {len(toks)}개 중 컬럼과 매칭 {len(matched)}개, 미매칭 {len(only_tok)}개\n")
    print("   [매칭됨]")
    for t, c in matched:
        mark = "" if t == c else f"  → {c}"
        print(f"     Data7_{t}{mark}")
    print("\n   [컬럼에 없는 토큰 — 별도 처리 필요]")
    for t in only_tok:
        print(f"     Data7_{t}")

conn.close()
print("\n이 결과로 3쪽 채우는 코드를 만듭니다.")
