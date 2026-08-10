#!/usr/bin/env python3
"""_trace_wht_item.py — 특정 항목이 서식까지 도달하는지 단계별 추적 (읽기 전용)

wht_watch(매핑 항목만 검사)는 이상 0명인데 _verify_wht_all(전 항목 검사)은
김미선 님에게 8건 미출력이라고 한다. 그중 5건은 매핑에 있는 항목이라
둘 중 하나가 틀렸다. 발급본 PDF 없이 어디서 끊기는지 직접 본다.

  ① ERP 원본        _TWPRAdjTotResultDtl 의 항목명과 금액
  ② 매핑 통과 여부   ERP_ITEM_NAME 에 걸리는가
  ③ 계산 결과 반영   r[키] 가 그 값으로 바뀌었는가
  ④ 서식 토큰        Data6_AmtNN 에 실렸는가
  ⑤ 최종 HTML       그 숫자가 실제로 찍혔는가

ERP 는 읽기만 한다.

사용:  python3 _archive/_trace_wht_item.py [--name 김미선] [--yy 2025]
"""
import argparse
import contextlib
import io
import os
import re
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
ap.add_argument("--name", default="김미선")
ap.add_argument("--yy", default="2025")
args = ap.parse_args()

WATCH = ["공제대상자녀(세액공제)", "보장성보험(세액공제)", "교육비(세액공제)",
         "의료비(세액공제)", "의료비(대상금액)", "본인의료비",
         "그밖의공제대상자의료비", "근로소득세액공제한도"]


def head(t):
    print(f"\n{'=' * 72}\n  {t}\n{'=' * 72}")


conn = W._conn()
cur = conn.cursor()
cur.execute("""SELECT EmpSeq, EmpID FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
r0 = cur.fetchone()
if not r0:
    sys.exit(f"{args.yy} 귀속에 '{args.name}' 없음")
emp_seq, emp_id = r0[0], str(r0[1]).strip()
print(f"대상: {args.name} · 사번 {emp_id} · {args.yy} 귀속")

# ① ERP 원본
head("① ERP 원본 (_TWPRAdjTotResultDtl)")
cur.execute("""SELECT i.AdjItemName, d.Amt, d.OrgAmt
               FROM _TWPRAdjTotResultDtl d
               LEFT JOIN _TWPRAdjTotItem i
                 ON i.YY=d.YY AND i.AdjItemSeq=d.AdjItemSeq
               WHERE d.YY=%s AND d.EmpSeq=%s""", (args.yy, emp_seq))
raw = {}
for nm, amt, org in cur.fetchall():
    nm = (nm or "").strip()
    raw[nm] = (int(float(amt or 0)), int(float(org or 0)))
for nm in WATCH:
    if nm in raw:
        a, o = raw[nm]
        print(f"  {nm:<28} Amt {a:>12,}   OrgAmt {o:>12,}")
    else:
        print(f"  {nm:<28} (ERP 에 없음)")

# ② 매핑
head("② ERP_ITEM_NAME 매핑 통과 여부")
for nm in WATCH:
    key = W.ERP_ITEM_NAME.get(nm)
    print(f"  {nm:<28} → {key if key is not None else '매핑 없음 (그래서 무시됨)'}")

# ③ load_erp_result 결과
head("③ load_erp_result() 가 실제로 돌려준 값")
got_erp = W.load_erp_result(cur, emp_seq, args.yy)
for k, v in sorted(got_erp.items(), key=lambda x: str(x[0])):
    print(f"  r[{k!r}] = {v:,}")
if not got_erp:
    print("  (비어 있음 — 매핑이 하나도 안 걸렸다는 뜻)")

# ④⑤ 렌더 후 HTML 확인
head("④⑤ 서식에 실제로 찍혔는가")
with contextlib.redirect_stdout(io.StringIO()) as buf:
    html, t, filled, missing = W.render(cur, emp_id, args.yy)
note = buf.getvalue().strip()
if note:
    print("  [렌더 중 메시지]")
    for line in note.splitlines():
        print("   " + line)
    print()

text = re.sub(r"<[^>]+>", " ", html)
nums = {int(m.replace(",", ""))
        for m in re.findall(r"-?\d{1,3}(?:,\d{3})+", text)}
for nm in WATCH:
    if nm not in raw:
        continue
    a = raw[nm][0]
    if abs(a) < 1000:
        continue
    print(f"  {nm:<28} {a:>12,}  {'✅ 서식에 있음' if a in nums else '❌ 서식에 없음'}")

head("판정")
mapped_missing = [nm for nm in WATCH
                  if nm in raw and W.ERP_ITEM_NAME.get(nm) is not None
                  and abs(raw[nm][0]) >= 1000 and raw[nm][0] not in nums]
if mapped_missing:
    print("  매핑돼 있는데 서식에 안 나오는 항목:")
    for nm in mapped_missing:
        print(f"    · {nm}  ({raw[nm][0]:,})")
    print("\n  → ③ 에 값이 있는데 ⑤ 에 없다면 서식 토큰 연결이 빠진 것이고,")
    print("     ③ 에도 없다면 항목명이 매핑 키와 다른 것이다.")
else:
    print("  매핑된 항목은 전부 서식에 나옵니다.")
    print("  _verify_wht_all 이 잡은 나머지는 ERP 내부 계산값입니다.")

conn.close()
