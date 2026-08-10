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
ap.add_argument("--limit", type=int, default=20)
ap.add_argument("--all", action="store_true")
ap.add_argument("--name", default="")
ap.add_argument("--min", type=int, default=1000,
                help="이 금액 미만은 무시 (자잘한 값 노이즈 제거)")
ap.add_argument("--calibrate", default="",
                help="이 사람 기준으로 잡음 항목 목록을 만든다 (발급본 검증된 사람)")
args = ap.parse_args()


IGNORE_FILE = os.path.join(HERE_ROOT, "wht_ignore_items.json")


def load_ignore():
    """서식에 인쇄되지 않는 ERP 내부 항목(AdjItemSeq) 목록.

    ERP 는 한도 계산용 중간값과 합산값도 저장한다. 예를 들어
    '결정세액계'(소득세+지방소득세)는 서식이 둘을 따로 찍으므로 합계는 안 나온다.
    이런 걸 오류로 잡으면 진짜 누락이 묻힌다.

    목록은 추측하지 않고, **발급본과 대조 검증된 사람**을 기준으로 산출한다
    (--calibrate). 그 사람의 서식은 ERP 발급본과 동일하므로, 거기서 안 나오는
    항목은 정의상 '인쇄되지 않는 항목'이다.
    """
    import json
    if not os.path.exists(IGNORE_FILE):
        return set(), {}
    try:
        d = json.load(open(IGNORE_FILE, encoding="utf-8"))
        return set(d.get("ignore_seq", [])), d.get("names", {})
    except Exception:
        return set(), {}


def head(t):
    print(f"\n{'=' * 76}\n  {t}\n{'=' * 76}")


IGNORE_SEQ, IGNORE_NAMES = load_ignore()

conn = W._conn()
cur = conn.cursor()

# AdjItemSeq → 항목명
cur.execute("SELECT AdjItemSeq, AdjItemName FROM _TWPRAdjTotItem WHERE YY=%s",
            (args.yy,))
ITEM = {r[0]: (r[1] or "").strip() for r in cur.fetchall()}

_who = args.calibrate or args.name
if _who:
    cur.execute("""SELECT EmpSeq, EmpID, EmpName FROM _TWPRAdjTotResult
                   WHERE YY=%s AND EmpName=%s""", (args.yy, _who))
else:
    cur.execute("""SELECT EmpSeq, EmpID, EmpName FROM _TWPRAdjTotResult
                   WHERE YY=%s ORDER BY EmpName""", (args.yy,))
rows = cur.fetchall()
if not rows:
    sys.exit("대상자가 없습니다.")

targets = rows if (args.all or _who) else \
    rows[::max(1, len(rows) // args.limit)][:args.limit]
print(f"{args.yy} 귀속 {len(rows)}명 중 {len(targets)}명 대조")
print(f"정답지: _TWPRAdjTotResultDtl (ERP 계산결과)\n")

_CAL_SEQS = set()
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
    if not args.calibrate and IGNORE_SEQ:
        # 서식에 인쇄되지 않는 ERP 내부 항목은 제외한다.
        # 어떤 값이 '무시 항목에서만' 나온다면 그건 서식에 없어도 정상이다.
        absent = [v for v in absent if not erp[v] <= IGNORE_SEQ]
    detail = []
    for v in absent:
        names = sorted({ITEM.get(s, f"seq{s}") for s in erp[v]})
        detail.append(f"{v:>12,}  {' / '.join(names)[:44]}")

    if args.calibrate:
        for v in absent:
            _CAL_SEQS.update(erp[v])

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

if args.calibrate:
    import json
    seqs = set()
    for _n, _e, detail in bad:
        for d in detail:
            pass
    # 위 detail 은 표시용이라 seq 를 다시 모은다
    seqs = sorted(_CAL_SEQS)
    json.dump({"calibrated_from": args.calibrate, "yy": args.yy,
               "ignore_seq": seqs,
               "names": {str(s_): ITEM.get(s_, "") for s_ in seqs}},
              open(IGNORE_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    head(f"잡음 목록 저장 — {len(seqs)}개 항목")
    for s_ in seqs:
        print(f"    seq {s_:<5} {ITEM.get(s_, '')}")
    print(f"\n  → {IGNORE_FILE}")
    print(f"  {args.calibrate} 는 발급본과 검증된 사람이므로,")
    print("  그의 서식에 안 나오는 항목은 '인쇄되지 않는 항목'으로 확정된다.")
    sys.exit(0)

head("요약")
print(f"  대조 {len(targets)}명 · 미출력 있는 사람 {len(bad)}명")
if IGNORE_SEQ:
    print(f"  (서식 미인쇄 항목 {len(IGNORE_SEQ)}개는 제외하고 판정)")
print("""
  읽는 법
    ERP항목  ERP 가 저장한 0 아닌 금액의 종류 수
    미출력   그중 우리 서식에 안 찍힌 것

  미출력이 있다고 전부 오류는 아니다. ERP 가 내부 계산용으로만 두고
  서식에는 인쇄하지 않는 중간값이 섞인다. 항목명을 보고 판단해야 한다.
  발급본에서 그 칸이 비어 있으면 정상, 값이 있으면 우리가 빠뜨린 것이다.
""")
