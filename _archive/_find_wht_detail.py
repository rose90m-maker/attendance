#!/usr/bin/env python3
"""_find_wht_detail.py — 원천징수영수증 3쪽 명세가 ERP 어느 테이블에 있는지 역추적 (읽기 전용)

3쪽 「소득공제 명세서」(신용카드 세부·보험료·의료비·교육비·기부금 명세)는
우리가 조회하는 _TWPRAdjTotIncomeTaxDeduc 에 합계만 있어 비어 있다.
그런데 ERP 발급본에는 전부 찍히므로 데이터는 ERP 안 어딘가에 있다.

발급본에서 읽은 **정확한 금액**으로 전체 테이블·컬럼을 훑어 그 값이 어디
저장돼 있는지 찾는다. 값이 특이해서 오탐이 거의 없다.

SELECT 와 INFORMATION_SCHEMA 조회만 한다.

사용:
  python3 _archive/_find_wht_detail.py                 지창구 2025
  python3 _archive/_find_wht_detail.py --name 홍길동 --yy 2024
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import wht_receipt as W
except Exception as e:
    sys.exit(f"wht_receipt 로드 실패: {e}\n  → 저장소 루트에서 실행하세요.")

ap = argparse.ArgumentParser()
ap.add_argument("--name", default="지창구")
ap.add_argument("--yy", default="2025")
ap.add_argument("--like", default="TWPRAdjTot",
                help="훑을 테이블 이름 패턴 (기본 TWPRAdjTot)")
args = ap.parse_args()

# ERP 발급본 3쪽에서 읽은 금액 — 이 값들이 어느 테이블에 있는지 찾는다
NEEDLES = [
    (45_356_285, "신용카드등 국세청 계"),
    (43_017_645, "지창구 국세청 신용카드"),
    (2_338_640,  "김채진 국세청 신용카드"),
    (1_212_565,  "대중교통 이용분"),
    (823_550,    "현금영수증"),
    (764_980,    "전통시장 사용분"),
    (5_461_938,  "보험료·의료비 국세청 계"),
    (3_823_080,  "보장성보험 국세청"),
    (866_000,    "기타 계"),
]


def head(t):
    print(f"\n{'=' * 68}\n  {t}\n{'=' * 68}")


conn = W._conn()
cur = conn.cursor()

# ── 대상자 ───────────────────────────────────────────────────
cur.execute("""SELECT EmpSeq, EmpID FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
row = cur.fetchone()
if not row:
    sys.exit(f"{args.yy} 귀속에 '{args.name}' 이 없습니다.")
emp_seq, emp_id = row[0], str(row[1]).strip()
print(f"대상: {args.name} · EmpSeq {emp_seq} · 사번 {emp_id} · {args.yy} 귀속")

# ── 후보 테이블 ──────────────────────────────────────────────
cur.execute("""SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
               WHERE TABLE_TYPE='BASE TABLE' AND TABLE_NAME LIKE %s
               ORDER BY TABLE_NAME""", ('%' + args.like + '%',))
tables = [r[0] for r in cur.fetchall()]
head(f"1. 후보 테이블 {len(tables)}개  (패턴: %{args.like}%)")
for t in tables:
    print(f"  {t}")

# ── 숫자 컬럼 훑기 ───────────────────────────────────────────
head("2. 발급본 금액이 들어 있는 테이블·컬럼 찾기")
print("  테이블마다 숫자 컬럼을 훑습니다. 잠시 걸립니다.\n")

NUMERIC = ("int", "bigint", "smallint", "decimal", "numeric", "money",
           "smallmoney", "float", "real")
hits = {}

for tbl in tables:
    cur.execute("""SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_NAME=%s""", (tbl,))
    cols = [c for c, d in cur.fetchall() if d.lower() in NUMERIC]
    if not cols:
        continue
    for val, label in NEEDLES:
        for col in cols:
            try:
                cur.execute(f"SELECT COUNT(*) FROM [{tbl}] WHERE [{col}] = %s", (val,))
                n = cur.fetchone()[0]
            except Exception:
                continue
            if n:
                hits.setdefault((tbl, col), []).append((val, label, n))

if not hits:
    print("  ❌ 아무 테이블에서도 못 찾았습니다.")
    print("     → --like 를 넓혀 보세요 (예: --like WPRAdj, --like Nts, --like Simpl)")
else:
    for (tbl, col), found in sorted(hits.items(), key=lambda x: -len(x[1])):
        print(f"  ✅ {tbl}.{col}")
        for val, label, n in found:
            print(f"       {val:>12,}  {label}  (행 {n}개)")

# ── 가장 유력한 테이블 상세 ──────────────────────────────────
if hits:
    best = max(hits.items(), key=lambda x: len(x[1]))[0][0]
    head(f"3. 유력 테이블 [{best}] — 컬럼 구조")
    cur.execute("""SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
                   FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME=%s
                   ORDER BY ORDINAL_POSITION""", (best,))
    for c, d, ln in cur.fetchall():
        print(f"    {c:<32}{d}{f'({ln})' if ln else ''}")

    head(f"4. [{best}] — {args.name} {args.yy} 실제 행")
    cur.execute("""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_NAME=%s AND COLUMN_NAME IN
                   ('YY','EmpSeq','EmpID')""", (best,))
    keys = [r[0] for r in cur.fetchall()]
    where, params = [], []
    if "YY" in keys:
        where.append("YY=%s"); params.append(args.yy)
    if "EmpSeq" in keys:
        where.append("EmpSeq=%s"); params.append(emp_seq)
    elif "EmpID" in keys:
        where.append("EmpID=%s"); params.append(emp_id)
    sql = f"SELECT TOP 40 * FROM [{best}]"
    if where:
        sql += " WHERE " + " AND ".join(where)
    try:
        cur.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print(f"    {len(rows)}행\n")
        print("    " + " | ".join(cols))
        for r in rows:
            print("    " + " | ".join(
                str(x).strip()[:18] if x is not None else "" for x in r))
    except Exception as e:
        print(f"    조회 실패: {e}")

# ── 3쪽 템플릿 토큰 ──────────────────────────────────────────
head("5. 3쪽 서식에 채워야 할 토큰")
try:
    cur.execute("""SELECT Page3 FROM _TWPRAdjTotAbrIncomeHTML WHERE YY=%s""",
                (args.yy,))
    r = cur.fetchone()
    if r and r[0]:
        import re
        toks = sorted(set(re.findall(r"Y[Ll][Ww]#_[A-Za-z0-9_]+", r[0])))
        print(f"    토큰 {len(toks)}개")
        for i in range(0, len(toks), 3):
            print("    " + "  ".join(f"{x:<28}" for x in toks[i:i + 3]))
    else:
        print("    Page3 서식이 비어 있습니다.")
except Exception as e:
    print(f"    조회 실패: {e}")

conn.close()
print("\n위 2·4번 결과를 알려주시면 3쪽 채우는 코드를 만들겠습니다.")
