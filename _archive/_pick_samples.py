#!/usr/bin/env python3
"""_pick_samples.py — 발급본을 누구 걸 뽑아야 전체가 덮이는지 고른다 (읽기 전용)

전 직원 발급본을 한 번에 못 뽑을 때를 위한 것이다.
189명이 쓰는 공제 항목은 겹치는 게 대부분이라, 잘 고르면 몇 장으로 전 항목이 덮인다.

ERP 가 그 사람에게 0 아닌 금액으로 갖고 있는 항목 = 그 사람 서식에 찍히는 항목이다.
이미 대조를 마친 사람이 덮은 항목을 빼고, 남은 항목을 가장 많이 덮는 사람부터
차례로 고른다(그리디 집합덮개). 그 목록대로 뽑으면 새 정보가 가장 빨리 쌓인다.

ERP 는 읽기만 한다.

사용:
  python3 _archive/_pick_samples.py
  python3 _archive/_pick_samples.py --done 지창구,김미선,이재현 --take 8
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
ap.add_argument("--done", default="지창구,김미선,이재현",
                help="이미 발급본과 대조를 마친 사람 (쉼표 구분)")
ap.add_argument("--take", type=int, default=8, help="몇 명까지 추천할지")
ap.add_argument("--min", type=int, default=1000)
args = ap.parse_args()

DONE = [x.strip() for x in args.done.split(",") if x.strip()]


def head(t):
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


conn = W._conn()
cur = conn.cursor()

cur.execute("SELECT AdjItemSeq, AdjItemName FROM _TWPRAdjTotItem WHERE YY=%s",
            (args.yy,))
ITEM = {r[0]: (r[1] or "").strip() for r in cur.fetchall()}

cur.execute("""SELECT EmpSeq, EmpID, EmpName FROM _TWPRAdjTotResult
               WHERE YY=%s ORDER BY EmpName""", (args.yy,))
emps = [(s, str(i).strip(), (n or "").strip()) for s, i, n in cur.fetchall()]

# 사람 → 0 아닌 금액을 가진 항목 집합
cur.execute("""SELECT EmpSeq, AdjItemSeq, Amt, OrgAmt
               FROM _TWPRAdjTotResultDtl WHERE YY=%s""", (args.yy,))
has = {}
for es, seq, amt, org in cur.fetchall():
    try:
        a = max(abs(float(amt or 0)), abs(float(org or 0)))
    except (TypeError, ValueError):
        continue
    if a >= args.min:
        has.setdefault(es, set()).add(seq)

# 종전근무지 보유 여부도 덮개 대상에 넣는다 (1쪽 열은 항목이 아니라 구조라서)
cur.execute("SELECT DISTINCT EmpSeq FROM _TWPRAdjTotPreWork WHERE YY=%s",
            (args.yy,))
movers = {r[0] for r in cur.fetchall()}
PREWORK = "__종전근무지__"
for es in movers:
    has.setdefault(es, set()).add(PREWORK)

by_name = {n: (s, i) for s, i, n in emps}
covered = set()
for nm in DONE:
    if nm in by_name:
        covered |= has.get(by_name[nm][0], set())

all_items = set().union(*has.values()) if has else set()
head(f"현황 ({args.yy} 귀속)")
print(f"  직원 {len(emps)}명 · 서식에 값이 실리는 항목 {len(all_items)}종")
print(f"  이미 대조한 {len(DONE)}명({', '.join(DONE)})이 덮은 항목 {len(covered)}종")
print(f"  남은 항목 {len(all_items - covered)}종")

head(f"추천 순서 — 이 순서로 뽑으면 가장 빨리 덮인다")
pool = {s: has.get(s, set()) for s, _i, _n in emps}
name_of = {s: n for s, _i, n in emps}
id_of = {s: i for s, i, _n in emps}
for nm in DONE:
    if nm in by_name:
        pool.pop(by_name[nm][0], None)

def label(x):
    return "종전근무지" if x == PREWORK else ITEM.get(x, f"seq{x}")


picked = []
cov = set(covered)
for _ in range(args.take):
    best, gain, newly = None, 0, set()
    for s, items in pool.items():
        g = items - cov
        if len(g) > gain:
            best, gain, newly = s, len(g), g
    if best is None:
        break
    cov |= pool.pop(best)
    picked.append((best, gain, newly, len(cov)))

if not picked:
    print("  더 뽑을 필요가 없습니다 — 이미 전 항목이 덮였습니다.")
else:
    print(f"  {'순':>2} {'사원':<10}{'사번':<12}{'새 항목':>7}{'누적':>9}  이 사람만 가진 항목")
    print("  " + "-" * 76)
    for k, (s, gain, newly, running) in enumerate(picked, 1):
        pct = 100 * running / max(1, len(all_items))
        names = ", ".join(label(x) for x in sorted(newly, key=str)[:4])
        print(f"  {k:>2} {name_of[s]:<10}{id_of[s]:<12}{gain:>7}"
              f"{running:>5}({pct:>3.0f}%)  {names[:42]}")

    print(f"\n  이 {len(picked)}명까지 뽑으면 {len(cov)}/{len(all_items)}종 "
          f"({100 * len(cov) / max(1, len(all_items)):.0f}%) 이 덮입니다.")

head("지금 발급해도 되는 사람 / 아직 위험한 사람")
print("  '검증됨' = 그 사람이 쓰는 항목이 전부 발급본과 대조된 것들이다.")
print("  '미검증 있음' = 한 번도 발급본으로 확인 안 된 항목이 섞여 있다.\n")
safe, risky = [], []
for s, i, n in emps:
    un = has.get(s, set()) - covered
    (risky if un else safe).append((n, i, un))
print(f"  검증됨      {len(safe):>4}명 / {len(emps)}명")
print(f"  미검증 있음 {len(risky):>4}명 / {len(emps)}명")
if risky:
    print("\n  미검증 항목이 섞인 사람 (앞 15명)")
    for n, i, un in sorted(risky, key=lambda x: -len(x[2]))[:15]:
        names = ", ".join(("종전근무지" if x == PREWORK else ITEM.get(x, f"seq{x}"))
                          for x in sorted(un, key=str)[:3])
        print(f"    {n:<10}{i:<12}{len(un):>3}종  {names[:46]}")

head("아직 아무도 안 덮은 항목 (발급본으로 확인 불가)")
rest = all_items - cov
if not rest:
    print("  없음")
else:
    for x in sorted(rest, key=str)[:30]:
        print(f"    {'종전근무지' if x == PREWORK else ITEM.get(x, f'seq{x}')}")
    if len(rest) > 30:
        print(f"    … 외 {len(rest) - 30}종")

conn.close()
print("\n전 직원을 한 번에 뽑을 수 있으면 그게 제일 좋습니다.")
print("안 되면 위 순서대로 몇 장만 뽑아 _diff_all_pdf.py 에 넘기세요.")
