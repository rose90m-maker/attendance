#!/usr/bin/env python3
"""_check_identities.py — 서식 산술 항등식 전수 검사 (읽기 전용, 발급본 불필요)

서식에는 스스로 검산되는 식이 인쇄돼 있다. 칸이 밀리거나 값이 빠지면 이 식이
깨지므로, 발급본 PDF 없이도 189명 전원의 내부 일관성을 확인할 수 있다.
(발급본 대조를 대체하지는 못한다 — 양변이 같이 틀리면 못 잡는다. 하지만
리포트 프로시저가 암호화라 실행할 수 없을 때 쓸 수 있는 가장 강한 전수 검사다.)

검사하는 식
  A. 1쪽 소득 각 행:  주(현) + 종(전)1~3 = 합계
  B. 16.계 행:        주(현) + 종(전)1~3 = 총계, 그리고 각 열이 ⑬~⑮ 행들의 합
  C. 21.총급여 = 16.계 총계
  D. 23.근로소득금액 = 21 - 22
  E. 48.과세표준 = 36.차감소득금액 - 46.그밖의공제계   (47.한도초과 있으면 빼고)
  F. 72.결정세액 = 49.산출세액 - 54.세액감면계 - 71.세액공제계  (음수면 0)
  G. 73.결정세액(소득세) = 72
  H. 77 = 73 - 74 - 75 - 76  (10원 미만 절사 허용)
  I. 65.특별세액공제 계 = 61~64 의 세액공제액 합
  J. 3쪽 국세청/기타 계 = 부양가족별 값의 세로 합

사용:
  python3 _archive/_check_identities.py            2025 전원
  python3 _archive/_check_identities.py --name 김미선
"""
import argparse
import contextlib
import io
import os
import sys
from decimal import Decimal

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
ap.add_argument("--name", default="")
ap.add_argument("--tol", type=int, default=0, help="허용 오차(원)")
args = ap.parse_args()


def n(v):
    """토큰 값('1,234' / '' / None) → int"""
    s = str(v or "").replace(",", "").strip()
    if not s or s == "-":
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def trunc10(x):
    return (1 if x >= 0 else -1) * (abs(x) // 10 * 10)


def head(t):
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


conn = W._conn()
cur = conn.cursor()

if args.name:
    cur.execute("""SELECT EmpSeq, EmpID, EmpName FROM _TWPRAdjTotResult
                   WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
else:
    cur.execute("""SELECT EmpSeq, EmpID, EmpName FROM _TWPRAdjTotResult
                   WHERE YY=%s ORDER BY EmpName""", (args.yy,))
emps = cur.fetchall()
if not emps:
    sys.exit("대상 없음")

head(f"산술 항등식 전수 검사 — {len(emps)}명 ({args.yy} 귀속)")

problems = []
render_fail = []
for emp_seq, emp_id, name in emps:
    emp_id, name = str(emp_id).strip(), (name or "").strip()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            t, v, depen = W.build_values(cur, emp_id, args.yy)
            inc = W.load_income(cur, t["emp_seq"], args.yy)
            pre = W.load_income_pre(cur, t["emp_seq"], args.yy)
            works = W.load_prework(cur, t["emp_seq"], args.yy)
            beg, end = W.load_period(cur, t["emp_seq"], args.yy)
            d2, d3, sums = W.build_income_rows(
                t, inc, pre, works, beg, end, W.load_company(cur))
    except Exception as e:
        render_fail.append((name, f"{type(e).__name__}: {str(e)[:80]}"))
        continue

    errs = []

    def eq(label, lhs, rhs, tol=args.tol):
        if abs(lhs - rhs) > tol:
            errs.append(f"{label}: {lhs:,} ≠ {rhs:,} (차이 {lhs - rhs:+,})")

    # A. 소득 각 행
    for row in d3:
        title = row["Data3_Title"].split()[0]
        eq(f"1쪽 {title} 행합", n(row["Data3_Cur"]) + sum(
            n(row[f"Data3_pre{k}"]) for k in (1, 2, 3)), n(row["Data3_TotAmt"]))
    # B. 16.계
    eq("16.계 행합", n(sums["Data3_SumCur"]) + sum(
        n(sums[f"Data3_Sumpre{k}"]) for k in (1, 2, 3)), n(sums["Data3_TotAmt"]))
    eq("16.계=행들의합(주현)", sum(n(r_["Data3_Cur"]) for r_ in d3),
       n(sums["Data3_SumCur"]))
    eq("16.계=행들의합(총계)", sum(n(r_["Data3_TotAmt"]) for r_ in d3),
       n(sums["Data3_TotAmt"]))
    # C·D
    eq("21=16.계", n(v.get("Data6_Amt1")), n(sums["Data3_TotAmt"]))
    eq("23=21-22", n(v.get("Data6_Amt3")),
       n(v.get("Data6_Amt1")) - n(v.get("Data6_Amt2")))
    # E (47 토큰이 없으므로 46 만; 차이가 나면 47 후보로 표시만)
    lhs = n(v.get("Data6_Amt52"))
    rhs = n(v.get("Data6_Amt27")) - n(v.get("Data6_Amt40"))
    if lhs != rhs:
        errs.append(f"48=36-46: {lhs:,} ≠ {rhs:,} "
                    f"(차이 {lhs - rhs:+,} — 47.한도초과액이면 정상일 수 있음)")
    # F·G
    f_lhs = n(v.get("Data6_Amt95"))          # 72
    f_rhs = max(0, n(v.get("Data6_Amt53")) - n(v.get("Data6_Amt58"))
                - n(v.get("Data6_Amt94")))
    eq("72=49-54-71", f_lhs, f_rhs)
    eq("73(소득세)=72", n(v.get("Data5_Tax_Final")), f_lhs)
    # H. 77 (소득세·지방소득세)
    for tag, fin, pre_t, tc, dec in (
            ("소득세", "Data5_Tax_Final", "Data5_Tax_Pre", "Data5_Tax_TC",
             "Data5_Tax_Deducted"),
            ("지방", "Data5_ResidTax_Final", "Data5_ResidTax_Pre",
             "Data5_ResidTax_TC", "Data5_ResidTax_Deducted")):
        raw = n(v.get(fin)) - sum(n(v.get(f"{pre_t}{k}")) for k in (1, 2, 3)) \
            - n(v.get(tc))
        got = n(v.get(dec))
        if got not in (raw, trunc10(raw)):
            errs.append(f"77({tag}): 계산 {raw:,} / 칸 {got:,}")
    # I. 65.계 = 61~64 세액공제액 (형식상 61,62,63 + 64 들)
    # Amt72=장애인전용보장성, Amt115/117=고향사랑기부금 — 처음 목록에서 빠져
    # 김재구가 오탐으로 잡혔었다 (2026-08-10)
    i_rhs = sum(n(v.get(k)) for k in
                ("Data6_Amt70", "Data6_Amt72", "Data6_Amt74", "Data6_Amt76",
                 "Data6_Amt78", "Data6_Amt80", "Data6_Amt82", "Data6_Amt100",
                 "Data6_Amt102", "Data6_Amt115", "Data6_Amt117"))
    eq("65.계=61~64합", n(v.get("Data6_Amt87")), i_rhs)
    # J. 3쪽 세로 합 — 합계행 토큰이 개인 값의 합과 같은가
    if depen:
        cols = {}
        for d_ in depen:
            for k, val in d_.items():
                if k in W.SKIP_COLS or isinstance(val, bool):
                    continue
                if isinstance(val, (int, float, Decimal)):
                    cols[k] = cols.get(k, 0) + int(val)
        for k, total in cols.items():
            tokv = v.get(f"Data7_Sum{k}")
            if tokv is None:
                continue
            if n(tokv) != total:
                errs.append(f"3쪽 {k} 합: 개인합 {total:,} ≠ 계 {n(tokv):,}")

    if errs:
        problems.append((name, emp_id, errs))

ok = len(emps) - len(problems) - len(render_fail)
print(f"  통과 {ok}명 · 항등식 위반 {len(problems)}명 · 렌더 실패 {len(render_fail)}명")

if problems:
    head("항등식 위반 상세")
    for name, emp_id, errs in problems[:30]:
        print(f"\n  ▸ {name} ({emp_id})")
        for e in errs[:8]:
            print(f"      {e}")
    if len(problems) > 30:
        print(f"\n  … 외 {len(problems) - 30}명")
if render_fail:
    head("렌더 실패")
    for name, e in render_fail:
        print(f"  {name}: {e}")

conn.close()

head("판정")
if not problems and not render_fail:
    print("  ✅ 전원 항등식 통과 — 서식 내부 일관성에 모순이 없습니다.")
    print("     (양변이 같이 틀리는 오류는 발급본 대조만 잡을 수 있습니다)")
sys.exit(1 if (problems or render_fail) else 0)
