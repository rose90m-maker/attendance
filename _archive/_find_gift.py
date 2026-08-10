#!/usr/bin/env python3
"""_find_gift.py — 64.기부금 세액공제의 ERP 항목명 찾기 (읽기 전용)

김미선 2025 발급본에서 64㉰ 특례기부금 세액공제액과 65.계가 **빈칸**인데
우리 서식은 계산기 값 1,500 을 찍는다 (10번째 결함, 2026-08-10 확정).
반면 강미예 2025 는 ERP 의 65.계(121,500)가 그 1,500 을 포함한다 — 즉 ERP 는
"공제가 실제 적용된 사람"에게만 기부금 항목을 두는 것으로 보인다.

고치는 규칙은 "64 계열 칸은 ERP 에 있으면 그 값, 없으면 빈칸" 이다.
그러려면 ERP 가 기부금 대상금액/세액공제액을 어떤 AdjItemName 으로 저장하는지
알아야 한다. 이 스크립트가 그걸 찾는다.

  ① 기부금이 이름에 든 ERP 항목 전체 (2025)
  ② 표본 인물별 값 — 강미예(적용됨) / 김미선(한도로 미적용) / 장용국(종교단체)
  ③ 우리 서식이 지금 64 계열 칸에 넣는 값 (계산기 출처)

ERP 는 읽기만 한다.

사용:  python3 _archive/_find_gift.py [--yy 2025]
"""
import argparse
import contextlib
import io
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
ap.add_argument("--names", default="강미예,김미선,장용국,이복우")
args = ap.parse_args()

GIFT_TOKENS = [
    ("64㉮ 정치자금 10만↓", "Data6_Amt77", "Data6_Amt78"),
    ("64㉮ 정치자금 10만↑", "Data6_Amt79", "Data6_Amt80"),
    ("64㉰ 특례기부금", "Data6_Amt96", "Data6_Amt82"),
    ("64㉲ 일반(종교외)", "Data6_Amt99", "Data6_Amt100"),
    ("64㉳ 일반(종교단체)", "Data6_Amt101", "Data6_Amt102"),
]


def head(t):
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


conn = W._conn()
cur = conn.cursor()

head("① 이름에 '기부' 가 든 ERP 항목 (2025 정의 전체)")
cur.execute("""SELECT AdjItemSeq, AdjItemName FROM _TWPRAdjTotItem
               WHERE YY=%s AND AdjItemName LIKE %s
               ORDER BY AdjItemSeq""", (args.yy, "%기부%"))
items = cur.fetchall()
for seq, nm in items:
    print(f"  {seq:>6}  {(nm or '').strip()}")
if not items:
    print("  (없음)")

for name in [x.strip() for x in args.names.split(",") if x.strip()]:
    cur.execute("""SELECT EmpSeq, EmpID FROM _TWPRAdjTotResult
                   WHERE YY=%s AND EmpName=%s""", (args.yy, name))
    r0 = cur.fetchone()
    if not r0:
        print(f"\n  ({name}: {args.yy} 귀속에 없음)")
        continue
    emp_seq, emp_id = r0[0], str(r0[1]).strip()

    head(f"② {name} — 기부금 관련 ERP 값")
    cur.execute("""SELECT i.AdjItemName, d.Amt, d.OrgAmt
                   FROM _TWPRAdjTotResultDtl d
                   LEFT JOIN _TWPRAdjTotItem i
                     ON i.YY=d.YY AND i.AdjItemSeq=d.AdjItemSeq
                   WHERE d.YY=%s AND d.EmpSeq=%s
                     AND (i.AdjItemName LIKE %s OR i.AdjItemName LIKE %s)""",
                (args.yy, emp_seq, "%기부%", "%특별세액공제%"))
    rows = [(nm, amt, org) for nm, amt, org in cur.fetchall()
            if (amt and float(amt)) or (org and float(org))]
    if not rows:
        print("  (0 아닌 기부금 항목 없음 — ERP 에 없다는 뜻)")
    for nm, amt, org in rows:
        print(f"  {(nm or '').strip():<44} Amt {int(float(amt or 0)):>10,} "
              f"OrgAmt {int(float(org or 0)):>10,}")

    print(f"\n  ③ 우리 서식이 지금 넣는 값")
    with contextlib.redirect_stdout(io.StringIO()):
        _t, v, _d = W.build_values(cur, emp_id, args.yy)
    for label, obj_t, amt_t in GIFT_TOKENS:
        o, a = v.get(obj_t, ""), v.get(amt_t, "")
        if str(o).strip() or str(a).strip():
            print(f"    {label:<22} 대상 {o or '-':>10}  공제 {a or '-':>10}")
    print(f"    {'65.계':<22} {'':>10}       {v.get('Data6_Amt87') or '(빈칸)':>10}")

conn.close()
print("""
① 의 항목명 중 '대상금액'과 '세액공제' 쌍을 ERP_ITEM_NAME 에 매핑하고,
64 계열 칸은 계산기 값 대신 ERP 값만 쓰도록 바꾼다 (없으면 빈칸).
김미선(미적용)은 빈칸, 강미예(적용)는 값이 나와야 발급본과 같아진다.""")
