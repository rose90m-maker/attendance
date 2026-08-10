#!/usr/bin/env python3
"""_check_prework.py — 종(전)근무지 수정 검증 (읽기 전용)

1쪽 근무처별 소득명세는 열이 주(현) / 종(전)×3 / 합계 로 갈린다.
ERP DB 자동검사는 대조 대상이 전부 '합계' 항목이라, 종(전) 열이 통째로 비어도
합계만 맞으면 통과한다. 그래서 여기서는 **열 단위로** 본다.

  ① 종전근무지가 있는 사람의 1쪽 열이 제대로 갈렸는가
  ② 주(현) + 종(전) = 합계 인가 (산술 검산)
  ③ 김미선 2025 는 발급본 실측값과 같은가
  ④ 종전근무지가 없는 사람은 예전과 똑같은가 (회귀 확인)

ERP 는 읽기만 한다.

사용:  python3 _archive/_check_prework.py [--yy 2025]
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
ap.add_argument("--plain", type=int, default=3,
                help="종전근무지 없는 사람 몇 명을 회귀 확인할지")
args = ap.parse_args()

# 발급본(RptWPRAdjTotAbrincomeDtl 2025, 김미선)에서 직접 읽은 값
EXPECT = {
    "Data3_SumCur": "22,346,350",
    "Data3_Sumpre1": "827,000",
    "Data3_TotAmt": "23,173,350",
    "Data5_NPTotAmt": "1,153,570",
    "Data5_NPCurAmt": "1,053,000",
    "Data5_MedTotAmt": "1,079,600",
    "Data5_MedCurAmt": "936,900",
    "Data5_HireTotAmt": "203,790",
    "Data5_HireCurAmt": "196,350",
    "Data5_P1BizNo": "301-81-46359",
    "Data5_Tax_TC": "279,900",
    "Data5_ResidTax_TC": "27,960",
}


def head(t):
    print(f"\n{'=' * 76}\n  {t}\n{'=' * 76}")


def money(s):
    s = str(s or "").replace(",", "").strip()
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


conn = W._conn()
cur = conn.cursor()

cur.execute("""SELECT DISTINCT p.EmpSeq FROM _TWPRAdjTotPreWork p
               WHERE p.YY=%s""", (args.yy,))
pre_seqs = {r[0] for r in cur.fetchall()}

cur.execute("""SELECT EmpSeq, EmpID, EmpName FROM _TWPRAdjTotResult
               WHERE YY=%s ORDER BY EmpName""", (args.yy,))
emps = [(s, str(i).strip(), (n or "").strip()) for s, i, n in cur.fetchall()]

movers = [e for e in emps if e[0] in pre_seqs]
plain = [e for e in emps if e[0] not in pre_seqs][:args.plain]

head(f"① 종전근무지가 있는 {len(movers)}명 — 1쪽 열")
bad = []
for emp_seq, emp_id, name in movers:
    with contextlib.redirect_stdout(io.StringIO()):
        _t, v, _d = W.build_values(cur, emp_id, args.yy)
        inc = W.load_income(cur, emp_seq, args.yy)
        pre = W.load_income_pre(cur, emp_seq, args.yy)
        works = W.load_prework(cur, emp_seq, args.yy)

    cols = [v.get(f"Data3_Sumpre{k}", "") for k in (1, 2, 3)]
    cur_v, tot_v = v.get("Data3_SumCur", ""), v.get("Data3_TotAmt", "")
    calc = money(cur_v) + sum(money(c) for c in cols)
    ok = calc == money(tot_v)
    if not ok:
        bad.append((name, calc, money(tot_v)))

    print(f"\n  ▸ {name} ({emp_id})  종전근무지 {len(works)}곳")
    for w in works:
        print(f"      {w['name']}  {w['biz_no']}  "
              f"{W._period(w['beg'], w['end'])}")
    print(f"      16.계   주(현) {cur_v:>14}  "
          f"종(전) {' / '.join(c or '-' for c in cols):<22} "
          f"합계 {tot_v:>14}  {'✅' if ok else '❌ 합이 안 맞음'}")
    print(f"      74.종(전) 사업자번호 {v.get('Data5_P1BizNo','') or '(빈칸)'}"
          f"   소득세 {v.get('Data5_Tax_Pre1','') or '-'}")
    print(f"      국민연금 {v.get('Data5_NPTotAmt',''):>12} "
          f"({v.get('Data5_NPCurAmt',''):>12})   "
          f"건강 {v.get('Data5_MedTotAmt',''):>10} "
          f"({v.get('Data5_MedCurAmt',''):>10})   "
          f"고용 {v.get('Data5_HireTotAmt',''):>9} "
          f"({v.get('Data5_HireCurAmt',''):>9})")

head("②-1 세액 검산 — 74/75 의 출처가 서로 맞는가")
print("  74.종(전) 은 _TWPRAdjTotPreWorkDtl, 75.주(현) 은 NtsIncomeSum 의")
print("  Amt-PreAmt 에서 온다. 두 출처가 어긋나면 77 이 안 맞는다.\n")
src, arith = [], []
for emp_seq, emp_id, name in movers:
    with contextlib.redirect_stdout(io.StringIO()):
        _t, v, _d = W.build_values(cur, emp_id, args.yy)
        pre = W.load_income_pre(cur, emp_seq, args.yy)
        works = W.load_prework(cur, emp_seq, args.yy)
    for label, key in (("소득세", "소득세"), ("지방소득세", "지방소득세")):
        from_sum = int(pre.get(key, 0))
        from_dtl = int(sum(w["amt"].get(key, 0) for w in works))
        if from_sum != from_dtl:
            src.append((name, label, from_sum, from_dtl))
            print(f"  ❌ {name} {label}: NtsIncomeSum.PreAmt {from_sum:,} "
                  f"≠ PreWorkDtl 합 {from_dtl:,}")
    # 73 - 74 - 75 - 76 = 77
    for tag, fin, pre_t, tc, dec in (
            ("소득세", "Data5_Tax_Final", "Data5_Tax_Pre", "Data5_Tax_TC",
             "Data5_Tax_Deducted"),
            ("지방소득세", "Data5_ResidTax_Final", "Data5_ResidTax_Pre",
             "Data5_ResidTax_TC", "Data5_ResidTax_Deducted")):
        got = money(v.get(fin, "")) \
            - sum(money(v.get(f"{pre_t}{k}", "")) for k in (1, 2, 3)) \
            - money(v.get(tc, ""))
        want = money(v.get(dec, ""))
        # 차감징수세액은 10원 미만을 절사한다. 박상현 2025 에서 소득세 4원·
        # 지방소득세 9원이 남았는데, 이는 ERP 의 절사이지 우리 오류가 아니다
        # (2026-08-10, 처음에 출처 불일치로 잘못 판단했다).
        trunc = (1 if got >= 0 else -1) * (abs(got) // 10 * 10)
        if got != want and trunc != want:
            arith.append((name, tag, got, want))
            print(f"  ❌ {name} {tag}: 73-74-75-76 = {got:,} "
                  f"이지만 77 칸은 {want:,} (절사로도 설명 안 됨)")
        elif got != want:
            print(f"  ℹ️  {name} {tag}: {got:,} → {want:,} "
                  f"(10원 미만 절사, 정상)")
if not src and not arith:
    print("  ✅ 5명 전원 두 출처가 일치하고 77 검산도 맞습니다.")

head("③ 김미선 2025 — 발급본 실측값과 대조")
km = [e for e in movers if e[2] == "김미선"]
miss = []
if not km:
    print("  김미선 없음 — 건너뜀")
else:
    with contextlib.redirect_stdout(io.StringIO()):
        _t, v, _d = W.build_values(cur, km[0][1], args.yy)
    print(f"  {'토큰':<22}{'발급본':>14}{'우리':>14}")
    for k, want in EXPECT.items():
        got = str(v.get(k, "")).strip()
        good = got == want
        if not good:
            miss.append((k, want, got))
        print(f"  {k:<22}{want:>14}{got:>14}  {'✅' if good else '❌'}")

head(f"④ 종전근무지 없는 {len(plain)}명 — 회귀 확인")
reg = []
for emp_seq, emp_id, name in plain:
    with contextlib.redirect_stdout(io.StringIO()):
        _t, v, _d = W.build_values(cur, emp_id, args.yy)
    c, t_, p1 = (v.get("Data3_SumCur", ""), v.get("Data3_TotAmt", ""),
                 v.get("Data3_Sumpre1", ""))
    ok = (c == t_) and not p1 and not v.get("Data5_P1BizNo", "")
    if not ok:
        reg.append(name)
    print(f"  {name:<10} 주(현) {c:>14}  합계 {t_:>14}  "
          f"종(전) {p1 or '(빈칸)':<10} {'✅' if ok else '❌'}")
    print(f"             국민연금 {v.get('Data5_NPTotAmt',''):>12} "
          f"({v.get('Data5_NPCurAmt',''):>12})")

conn.close()

head("판정")
print(f"  ① 종전근무지 보유 {len(movers)}명 · 합계 검산 실패 {len(bad)}명")
for nm, a, b in bad:
    print(f"      {nm}: 주(현)+종(전) {a:,} ≠ 합계 {b:,}")
print(f"  ③ 발급본 대조 불일치 {len(miss)}건")
for k, want, got in miss:
    print(f"      {k}: 발급본 {want} / 우리 '{got}'")
print(f"  ②-1 74/75 출처 불일치 {len(src)}건 · 77 검산 실패 {len(arith)}건")
print(f"  ④ 회귀 이상 {len(reg)}명 {reg if reg else ''}")
if not bad and not miss and not reg and not src and not arith:
    print("\n  전부 통과 — 종(전)근무지 열이 발급본과 같아졌습니다.")
    sys.exit(0)
# 실패는 종료코드로 알린다. 그래야 `검증 && 배포` 가 실제로 막힌다.
sys.exit(1)
