#!/usr/bin/env python3
"""_find_wht_result.py — ERP 가 '계산 결과'를 저장하는 테이블 찾기 (읽기 전용)

wht_receipt.py 헤더는 "2쪽 정산명세의 계산값은 ERP 가 출력 시점에 수식엔진으로
계산하며 DB 에 저장돼 있지 않다" 고 단정한다. 그런데 3쪽 명세도 같은 이유로
비워뒀다가 _TWPRAdjTotEmpDepenList 에서 발견됐다. 전제를 다시 의심한다.

지창구 2025 의 계산값(ERP 발급본으로 확인된 것)으로 전 테이블을 훑는다.
찾으면 189명 전원을 ERP 자체 값과 자동 대조할 수 있다 — 발급본 PDF 가 필요 없어진다.

ERP 는 읽기만 한다.

사용:  python3 _archive/_find_wht_result.py [--name 지창구] [--yy 2025]
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

import wht_receipt as W

ap = argparse.ArgumentParser()
ap.add_argument("--name", default="지창구")
ap.add_argument("--yy", default="2025")
ap.add_argument("--like", default="TWPRAdjTot")
args = ap.parse_args()

# ERP 발급본에서 확인된 '계산 결과'. 대상금액이 아니라 산식을 거친 값이라
# 이게 DB 에 있으면 ERP 가 결과도 저장한다는 뜻이다.
NEEDLES = [
    (12_781_038, "22.근로소득공제"),
    (47_839_732, "23.근로소득금액"),
    (31_657_791, "48.과세표준"),
    (3_488_668,  "49.산출세액"),
    (2_115_426,  "73.결정세액"),
    (211_542,    "73.지방소득세"),
    (417_797,    "62.의료비 세액공제액"),
    (660_000,    "55.근로소득 세액공제"),
    (3_476_833,  "41.신용카드 공제액"),
]

NUMERIC = ("int", "bigint", "smallint", "decimal", "numeric", "money",
           "smallmoney", "float", "real")


def head(t):
    print(f"\n{'=' * 70}\n  {t}\n{'=' * 70}")


conn = W._conn()
cur = conn.cursor()

cur.execute("""SELECT EmpSeq, EmpID FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
r = cur.fetchone()
if not r:
    sys.exit(f"{args.yy} 귀속에 '{args.name}' 없음")
emp_seq = r[0]
print(f"대상: {args.name} · EmpSeq {emp_seq} · {args.yy} 귀속")
print("찾는 값: 근로소득공제·과세표준·산출세액·결정세액 등 '계산 결과' 9종")

cur.execute("""SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
               WHERE TABLE_TYPE='BASE TABLE' AND TABLE_NAME LIKE %s
               ORDER BY TABLE_NAME""", ('%' + args.like + '%',))
tables = [t[0] for t in cur.fetchall()]
print(f"후보 테이블 {len(tables)}개 — 훑는 중…\n")

hits = {}
for tbl in tables:
    cur.execute("""SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_NAME=%s""", (tbl,))
    cols = [c for c, d in cur.fetchall() if d.lower() in NUMERIC]
    for val, label in NEEDLES:
        for col in cols:
            try:
                cur.execute(f"SELECT COUNT(*) FROM [{tbl}] WHERE [{col}] = %s", (val,))
                n = cur.fetchone()[0]
            except Exception:
                continue
            if n:
                hits.setdefault((tbl, col), []).append((val, label, n))

head("계산 결과가 들어 있는 테이블·컬럼")
if not hits:
    print("  ❌ 못 찾았습니다.")
    print("     → ERP 가 정말 계산값을 저장하지 않는 것으로 보입니다.")
    print("       이 경우 자체 검증은 불가능하고 발급본 대조가 유일한 방법입니다.")
    print("     → --like 를 넓혀 한 번 더 확인해 보세요 (--like WPR, --like Adj)")
else:
    ranked = sorted(hits.items(), key=lambda x: -len(x[1]))
    for (tbl, col), found in ranked:
        print(f"\n  ✅ {tbl}.{col}   ({len(found)}종 일치)")
        for val, label, n in found:
            print(f"       {val:>13,}  {label}  (행 {n})")

    best_tbl = ranked[0][0][0]
    head(f"유력 테이블 [{best_tbl}] — 컬럼 구조")
    cur.execute("""SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_NAME=%s ORDER BY ORDINAL_POSITION""", (best_tbl,))
    for c, d in cur.fetchall():
        print(f"    {c:<34}{d}")

    head(f"[{best_tbl}] — {args.name} 행 (최대 40)")
    cur.execute("""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_NAME=%s AND COLUMN_NAME IN ('YY','EmpSeq')""",
                (best_tbl,))
    keys = [x[0] for x in cur.fetchall()]
    where = " AND ".join(f"{k}=%s" for k in keys)
    params = tuple(args.yy if k == "YY" else emp_seq for k in keys)
    try:
        cur.execute(f"SELECT TOP 40 * FROM [{best_tbl}]"
                    + (f" WHERE {where}" if keys else ""), params)
        names = [d[0] for d in cur.description]
        print("    " + " | ".join(names))
        for row in cur.fetchall():
            print("    " + " | ".join(
                (f"{int(x):,}" if isinstance(x, (int, float)) and abs(x) > 999
                 else str(x).strip()[:16]) if x is not None else ""
                for x in row))
    except Exception as e:
        print(f"    조회 실패: {e}")

conn.close()
print("\n찾았으면 189명 전원 자동 대조가 가능합니다.")
