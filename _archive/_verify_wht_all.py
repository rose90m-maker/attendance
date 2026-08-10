#!/usr/bin/env python3
"""_verify_wht_all.py — 전 직원 영수증을 ERP 계산결과와 자동 대조 (읽기 전용)

ERP 는 계산 결과를 _TWPRAdjTotResultDtl 에 저장한다 (2026-08-10 발견).
  AdjItemSeq 별로  Amt = 한도 적용 후 공제금액,  OrgAmt = 대상금액
  예) seq 57 보장성보험 → OrgAmt 5,461,938 → Amt 1,000,000 (한도)

그래서 발급본 PDF 없이도 **ERP 자체 값을 정답지로** 쓸 수 있다.
직원별로 영수증을 렌더해서, ERP 가 가진 금액이 서식에 실제로 찍히는지 본다.

  · ERP 값이 서식에 없다  → 우리가 그 항목을 빠뜨렸다
  · 항목명(AdjItemName)으로 무엇이 빠졌는지 바로 나온다

ERP 는 읽기만 한다.

사용:
  python3 _archive/_verify_wht_all.py                 2025, 20명
  python3 _archive/_verify_wht_all.py --all
  python3 _archive/_verify_wht_all.py --name 박영규    한 명 상세
"""
import argparse
import contextlib
import io
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
ap.add_argument("--yy", default="2025")
ap.add_argument("--limit", type=int, default=20)
ap.add_argument("--all", action="store_true")
ap.add_argument("--name", default="")
ap.add_argument("--min", type=int, default=1000,
                help="이 금액 미만은 무시 (자잘한 값 노이즈 제거)")
args = ap.parse_args()


def head(t):
    print(f"\n{'=' * 76}\n  {t}\n{'=' * 76}")


conn = W._conn()
cur = conn.cursor()

# AdjItemSeq → 항목명
cur.execute("SELECT AdjItemSeq, AdjItemName FROM _TWPRAdjTotItem WHERE YY=%s",
            (args.yy,))
ITEM = {r[0]: (r[1] or "").strip() for r in cur.fetchall()}

if args.name:
    cur.execute("""SELECT EmpSeq, EmpID, EmpName FROM _TWPRAdjTotResult
                   WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
else:
    cur.execute("""SELECT EmpSeq, EmpID, EmpName FROM _TWPRAdjTotResult
                   WHERE YY=%s ORDER BY EmpName""", (args.yy,))
rows = cur.fetchall()
if not rows:
    sys.exit("대상자가 없습니다.")

targets = rows if (args.all or args.name) else \
    rows[::max(1, len(rows) // args.limit)][:args.limit]
print(f"{args.yy} 귀속 {len(rows)}명 중 {len(targets)}명 대조")
print(f"정답지: _TWPRAdjTotResultDtl (ERP 계산결과)\n")

bad = []
print(f"  {'사원':<9}{'ERP항목':>7}{'미출력':>7}  판정")
print("  " + "-" * 68)

for emp_seq, emp_id, name in targets:
    emp_id = str(emp_id).strip()
    name = (name or "").strip()

    cur.execute("""SELECT AdjItemSeq, Amt, OrgAmt FROM _TWPRAdjTotResultDtl
                   WHERE YY=%s AND EmpSeq=%s""", (args.yy, emp_seq))
    erp = {}
    for seq, amt, org in cur.fetchall():
        for v in (amt, org):
            try:
                iv = int(float(v or 0))
            except (TypeError, ValueError):
                continue
            if abs(iv) >= args.min:
                erp.setdefault(iv, set()).add(seq)

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            html, t, filled, missing = W.render(cur, emp_id, args.yy)
    except Exception as e:
        print(f"  {name:<9}{'':>7}{'':>7}  ❌ 렌더 실패: {type(e).__name__}")
        bad.append((name, emp_id, [f"렌더 실패: {e}"]))
        continue

    text = re.sub(r"<[^>]+>", " ", html)
    got = set()
    for m in re.findall(r"-?\d{1,3}(?:,\d{3})+", text):
        got.add(int(m.replace(",", "")))

    absent = sorted((v for v in erp if v not in got), key=lambda x: -abs(x))
    detail = []
    for v in absent:
        names = sorted({ITEM.get(s, f"seq{s}") for s in erp[v]})
        detail.append(f"{v:>12,}  {' / '.join(names)[:44]}")

    mark = "✅" if not absent else "⚠️ "
    print(f"  {name:<9}{len(erp):>7}{len(absent):>7}  {mark}"
          f"{'' if not absent else detail[0][:46]}")
    if absent:
        bad.append((name, emp_id, detail))

conn.close()

head("서식에 안 나오는 ERP 금액")
if not bad:
    print("  없음 — ERP 가 가진 금액이 전원 서식에 모두 출력됩니다.")
else:
    for name, emp_id, detail in bad[:25]:
        print(f"\n  ▸ {name} ({emp_id})")
        for d in detail[:12]:
            print(f"      {d}")
        if len(detail) > 12:
            print(f"      … 외 {len(detail)-12}건")
    if len(bad) > 25:
        print(f"\n  … 외 {len(bad)-25}명")

head("요약")
print(f"  대조 {len(targets)}명 · 미출력 있는 사람 {len(bad)}명")
print("""
  읽는 법
    ERP항목  ERP 가 저장한 0 아닌 금액의 종류 수
    미출력   그중 우리 서식에 안 찍힌 것

  미출력이 있다고 전부 오류는 아니다. ERP 가 내부 계산용으로만 두고
  서식에는 인쇄하지 않는 중간값이 섞인다. 항목명을 보고 판단해야 한다.
  발급본에서 그 칸이 비어 있으면 정상, 값이 있으면 우리가 빠뜨린 것이다.
""")
