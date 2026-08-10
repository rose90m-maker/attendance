#!/usr/bin/env python3
"""_check_wht_erp.py — 원천징수영수증 계산값을 ERP 기준과 대조 (읽기 전용)

■ 왜 DB 대 DB 비교가 안 되는가
ERP 는 소득공제 '대상금액'만 저장하고, 근로소득공제·과세표준·산출세액·결정세액
같은 계산값은 **출력 시점에 수식 엔진으로 만들며 DB 에 남기지 않는다**
(wht_receipt.py 헤더 참조). 그래서 대조 기준은 ERP 실발급본뿐이고,
`wht_calc.py` 는 지창구 2025 발급본에 맞춰 역공학된 것이다.

■ 이 스크립트가 하는 일
  1. ERP 가 실제로 갖고 있는 **입력값**을 그대로 보여준다 (총급여·기납부세액·공제 대상금액)
  2. 그 입력으로 wht_calc 를 돌린 **계산값 전체**를 서식 번호순으로 보여준다
  3. ERP 공제 항목 중 **우리 매퍼가 못 알아본 것(unmapped)** 을 드러낸다
     → 이게 있으면 그 금액이 조용히 누락된다. 가장 위험한 지점이다.
  4. 지창구 2025 는 발급본 기준값이 문서화돼 있어 **자동 합격/불합격 판정**을 한다

SELECT 만 하며 아무것도 쓰지 않는다.

사용:
  python3 _archive/_check_wht_erp.py                     지창구 2025
  python3 _archive/_check_wht_erp.py --name 홍길동 --yy 2024
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
    from wht_calc import compute
except Exception as e:
    sys.exit(f"모듈 로드 실패: {type(e).__name__}: {e}\n"
             f"  → 저장소 루트에서 실행해야 합니다.")

ap = argparse.ArgumentParser()
ap.add_argument("--name", default="지창구")
ap.add_argument("--yy", default="2025")
args = ap.parse_args()

# wht_calc.py docstring 에 기록된 지창구 2025 발급본 기준값
REFERENCE = {
    ("지창구", "2025"): [
        ("근로소득공제", 12_781_038),
        ("과세표준", 31_657_791),
        ("산출세액", 3_488_668),
        ("신용카드등 공제", 3_476_833),
        ("의료비 세액공제", 417_797),
        ("결정세액", 2_115_426),
        ("차감징수세액", -1_017_740),
    ],
}


def head(t):
    print(f"\n{'=' * 66}\n  {t}\n{'=' * 66}")


def won(v):
    try:
        return f"{int(v):>15,}"
    except (TypeError, ValueError):
        return f"{str(v):>15}"


try:
    conn = W._conn()
except Exception as e:
    sys.exit(f"ERP(MSSQL) 접속 실패: {type(e).__name__}: {e}\n"
             f"  → .env 의 ERP_DB_* 설정과 네트워크를 확인하세요.")
cur = conn.cursor()

# ── 사번 조회 ────────────────────────────────────────────────
cur.execute("""SELECT EmpID, EmpName, DeptName FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
found = cur.fetchall()
if not found:
    print(f"❌ {args.yy} 귀속 연말정산 대상에 '{args.name}' 이 없습니다.")
    cur.execute("SELECT COUNT(*) FROM _TWPRAdjTotResult WHERE YY=%s", (args.yy,))
    print(f"   해당 연도 전체 대상자: {cur.fetchone()[0]}명")
    conn.close(); sys.exit(1)
if len(found) > 1:
    print(f"⚠️  동명이인 {len(found)}명: " +
          ", ".join(f"{r[0]}({r[2]})" for r in found))
emp_no = str(found[0][0]).strip()
print(f"대상: {args.name} · 사번 {emp_no} · {found[0][2]} · {args.yy} 귀속")

# ── ERP 입력값 ───────────────────────────────────────────────
t = W.find_target(cur, emp_no, args.yy)
inc = W.load_income(cur, t["emp_seq"], args.yy)
deducs, unmapped = W.load_deducs(cur, t["emp_seq"], args.yy)
persons = W.load_persons(cur, t["emp_seq"], args.yy)

head("1. ERP 가 저장하고 있는 입력값")
pay = inc.get("급여", 0)
bonus = inc.get("상여", 0)
print(f"  급여                {won(pay)}")
print(f"  상여                {won(bonus)}")
print(f"  총급여(계)          {won(pay + bonus)}")
print(f"  기납부 소득세       {won(inc.get('소득세', 0))}")
print(f"  기납부 지방소득세   {won(inc.get('지방소득세', 0))}")
print("\n  [소득공제 대상금액 — 0 이 아닌 것만]")
for k, val in sorted(deducs.items()):
    if val:
        print(f"    {k:<24}{won(val)}")
print("\n  [인적공제 인원]")
for k, val in sorted(persons.items()):
    print(f"    {k:<24}{str(val):>15}")

# ── 미매핑 항목 (가장 중요) ──────────────────────────────────
head("2. 매퍼가 못 알아본 ERP 공제 항목")
if unmapped:
    print("  ⚠️  아래 항목은 계산에 반영되지 않았습니다. 금액이 누락됩니다.")
    total_lost = 0
    for item in unmapped:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            nm, amt = item
            total_lost += int(amt or 0)
            print(f"    - {str(nm):<30}{won(amt)}")
        else:
            print(f"    - {item}")
    if total_lost:
        print(f"    {'미반영 합계':<32}{won(total_lost)}")
    print("\n  → wht_receipt.py 의 load_deducs() 분류 규칙에 추가해야 합니다.")
else:
    print("  ✅ 없음 — ERP 공제 항목이 모두 분류됐습니다.")

# ── 계산값 ───────────────────────────────────────────────────
calc_in = dict(gross=pay + bonus,
               prepaid_tax=inc.get("소득세", 0),
               prepaid_local=inc.get("지방소득세", 0),
               **persons, **deducs)
r = compute(calc_in)

head("3. wht_calc 계산 결과 (서식 항목번호순)")


def _sortkey(k):
    s = str(k)
    num = "".join(ch for ch in s if ch.isdigit())
    return (int(num) if num else 999, s)


for k in sorted(r.keys(), key=_sortkey):
    print(f"  {str(k):<10}{won(r[k])}")

# ── 기준값 대조 ──────────────────────────────────────────────
head("4. ERP 실발급본 기준값 대조")
ref = REFERENCE.get((args.name, args.yy))
if not ref:
    print(f"  {args.name} {args.yy} 는 문서화된 기준값이 없습니다.")
    print("  → ERP 에서 원천징수영수증을 발급해 위 3번 값과 눈으로 대조하세요.")
    print("     일치하면 그 값을 이 스크립트의 REFERENCE 에 추가해 두면")
    print("     다음부터 자동 검증됩니다.")
else:
    values = {}
    for k, val in r.items():
        try:
            values.setdefault(int(val), []).append(str(k))
        except (TypeError, ValueError):
            pass
    ok = 0
    for label, expect in ref:
        keys = values.get(expect)
        if keys:
            print(f"  ✅ {label:<16}{won(expect)}   ← {', '.join(keys)}")
            ok += 1
        else:
            print(f"  ❌ {label:<16}{won(expect)}   계산값에 없음")
    print(f"\n  {ok}/{len(ref)} 일치")
    if ok == len(ref):
        print("  발급본과 동일합니다. 계산 엔진 정상.")
    else:
        print("  ⚠️  불일치가 있습니다. 위 1번 입력값부터 확인하세요 —")
        print("      ERP 쪽 대상금액이 바뀌었는지, 매퍼가 항목을 놓쳤는지(2번)")
        print("      순서로 보면 원인이 빨리 잡힙니다.")

conn.close()
