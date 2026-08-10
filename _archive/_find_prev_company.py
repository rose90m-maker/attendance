#!/usr/bin/env python3
"""_find_prev_company.py — 종(전)근무지 데이터가 ERP 어디에 있는지 찾기 (읽기 전용)

김미선 2025 발급본에 종(전)근무지(삼미음향기술㈜)가 찍혀 있는데 우리 서식은
그 열을 통째로 비워 둔다. build_income_rows() 가 Data2_pre1~3 / Data3_pre1~3 을
빈 문자열로 하드코딩해 놨기 때문이다.

합계 칸도 같이 틀어진다. load_income() 이 SMPerCoAllType=3502001(당사분)만 읽어서
16.계 가 22,346,350 으로 나오는데 발급본은 23,173,350 이다. 같은 장의 21.총급여와
어긋나므로 눈에 띄는 오류다.

ERP DB 자동검사(wht_watch)는 이걸 못 잡는다. 대조 대상이 전부 '합계' 항목이라
열이 비어 있어도 합계만 맞으면 통과한다. 그래서 발급본이 필요했다.

이 스크립트는 고치기 전에 필요한 사실만 모은다.
  ① SMPerCoAllType 코드값이 각각 무엇인가 (당사/종전/합계)
  ② 종전근무지 회사명·사업자번호·근무기간은 어느 테이블에 있는가
  ③ 2025 귀속에 종전근무지가 있는 사람이 몇 명인가
  ④ 하단 영수란의 (현근무지) 괄호값은 어디서 오는가

ERP 는 읽기만 한다.

사용:  python3 _archive/_find_prev_company.py [--name 김미선] [--yy 2025]
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
ap.add_argument("--name", default="김미선")
ap.add_argument("--yy", default="2025")
args = ap.parse_args()


def head(t):
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


def safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        print(f"   조회 실패: {type(e).__name__}: {str(e)[:140]}")
        return None


conn = W._conn()
cur = conn.cursor()

cur.execute("""SELECT EmpSeq, EmpID FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
r0 = cur.fetchone()
if not r0:
    sys.exit(f"{args.yy} 귀속에 '{args.name}' 없음")
emp_seq, emp_id = r0[0], str(r0[1]).strip()
print(f"대상: {args.name} · 사번 {emp_id} · {args.yy} 귀속")

# ── ① SMPerCoAllType 코드값 ─────────────────────────────────
head("① SMPerCoAllType 코드별 분포")
cur.execute("""SELECT SMPerCoAllType, COUNT(*), COUNT(DISTINCT EmpSeq)
               FROM _TWPRAdjTotNtsIncomeSum WHERE YY=%s
               GROUP BY SMPerCoAllType ORDER BY SMPerCoAllType""", (args.yy,))
codes = cur.fetchall()
print(f"  {'코드':>10}{'행수':>10}{'인원':>8}")
for c, n, e in codes:
    mark = "  ← 지금 쓰는 값(당사분)" if str(c) == "3502001" else ""
    print(f"  {str(c):>10}{n:>10,}{e:>8}{mark}")

# 코드 이름이 코드테이블에 있으면 같이 본다
head("코드 이름 (SM 코드테이블 추정)")
for tbl, keycol, namecol in (("_TSMSmallCode", "SmallCodeSeq", "SmallCodeName"),
                             ("_TSMCode", "CodeSeq", "CodeName"),
                             ("_TSMSmallCodeLang", "SmallCodeSeq", "SmallCodeName")):
    try:
        vals = ",".join(str(c[0]) for c in codes)
        cur.execute(f"SELECT {keycol}, {namecol} FROM [{tbl}] "
                    f"WHERE {keycol} IN ({vals})")
        got = cur.fetchall()
        if got:
            print(f"  {tbl}")
            for k, v in got:
                print(f"    {k}  {v}")
            break
    except Exception:
        continue
else:
    print("  (코드 이름 테이블을 못 찾음 — 금액으로 판별하면 된다)")

# ── ② 대상자의 코드별 금액 ──────────────────────────────────
head(f"② {args.name} 의 코드별 소득 금액")
cur.execute("""SELECT s.SMPerCoAllType, i.NtsItemName, s.Amt
               FROM _TWPRAdjTotNtsIncomeSum s
               JOIN (SELECT DISTINCT YY, NtsItemSeq, NtsItemName
                     FROM _TWPRAdjTotNtsItem) i
                 ON i.YY=s.YY AND i.NtsItemSeq=s.NtsItemSeq
               WHERE s.YY=%s AND s.EmpSeq=%s
               ORDER BY s.SMPerCoAllType, i.NtsItemName""", (args.yy, emp_seq))
cur_code = None
for c, nm, amt in cur.fetchall():
    try:
        iv = int(float(amt or 0))
    except (TypeError, ValueError):
        continue
    if iv == 0:
        continue
    if c != cur_code:
        cur_code = c
        print(f"\n  ▸ 코드 {c}")
    print(f"      {nm:<28}{iv:>14,}")
print("\n  발급본 대조표 (김미선 2025)")
print("      주(현) 급여 20,219,760 · 상여 2,126,590 · 계 22,346,350")
print("      종(전) 급여    827,000 ·                 계    827,000")
print("      합계   급여 21,046,760 · 상여 2,126,590 · 계 23,173,350")
print("      국민연금 1,153,570 (1,053,000) · 건강 1,079,600 (936,900)"
      " · 고용 203,790 (196,350)")

# ── ③ 종전근무지 회사정보 테이블 찾기 ───────────────────────
head("③ 종전근무지 회사명·사업자번호가 있는 테이블")
cur.execute("""SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
               WHERE TABLE_NAME LIKE '%AdjTot%'
                 AND (COLUMN_NAME LIKE '%BizNo%' OR COLUMN_NAME LIKE '%CoNm%'
                      OR COLUMN_NAME LIKE '%CompanyNm%' OR COLUMN_NAME LIKE '%CONM%'
                      OR COLUMN_NAME LIKE '%PreCo%' OR COLUMN_NAME LIKE '%BefCo%')
               ORDER BY TABLE_NAME, COLUMN_NAME""")
hits = cur.fetchall()
if not hits:
    print("  (이름으로는 못 찾음)")
else:
    seen = []
    for tbl, col in hits:
        if tbl not in seen:
            seen.append(tbl)
        print(f"  {tbl:<40}{col}")

    for tbl in seen:
        print(f"\n  ── {tbl} · {args.name} 의 행 ──")
        try:
            cur.execute(f"SELECT COUNT(*) FROM [{tbl}] WHERE YY=%s AND EmpSeq=%s",
                        (args.yy, emp_seq))
            n = cur.fetchone()[0]
            print(f"     행수 {n}")
            if n:
                cur.execute(f"SELECT TOP 5 * FROM [{tbl}] WHERE YY=%s AND EmpSeq=%s",
                            (args.yy, emp_seq))
                cols = [d[0] for d in cur.description]
                for row in cur.fetchall():
                    for c, v in zip(cols, row):
                        s = str(v).strip() if v is not None else ""
                        if s and s not in ("0", "0.0000", "0.00"):
                            print(f"       {c:<26}{s[:50]}")
                    print("       " + "-" * 40)
        except Exception as e:
            print(f"     조회 실패: {str(e)[:110]}")

# ── ④ 사업자번호 문자열로 직접 역추적 ───────────────────────
head("④ '301-81-46359' 가 저장된 곳 (발급본의 종전근무지 사업자번호)")
BIZ = "301-81-46359"
BIZ2 = BIZ.replace("-", "")
cur.execute("""SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
               WHERE DATA_TYPE IN ('nvarchar','varchar','nchar','char')
                 AND TABLE_NAME LIKE '%Adj%'""")
found = 0
for tbl, col in cur.fetchall():
    try:
        cur.execute(f"SELECT COUNT(*) FROM [{tbl}] WHERE [{col}] IN (%s, %s)",
                    (BIZ, BIZ2))
        n = cur.fetchone()[0]
    except Exception:
        continue
    if n:
        found += 1
        print(f"  ✅ {tbl}.{col}   ({n}행)")
if not found:
    print("  ❌ 사업자번호 문자열을 못 찾음 — 다른 형식으로 저장돼 있을 수 있음")

# ── ⑤ 영향 인원 ─────────────────────────────────────────────
head("⑤ 종전근무지가 있는 인원 (당사분 ≠ 전체)")
other = [c[0] for c in codes if str(c[0]) != "3502001"]
if not other:
    print("  당사분 코드 하나뿐입니다.")
else:
    ph = ",".join(["%s"] * len(other))
    cur.execute(f"""SELECT COUNT(DISTINCT s.EmpSeq)
                    FROM _TWPRAdjTotNtsIncomeSum s
                    WHERE s.YY=%s AND s.SMPerCoAllType IN ({ph})
                      AND s.Amt <> 0""", tuple([args.yy] + other))
    n = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM _TWPRAdjTotResult WHERE YY=%s", (args.yy,))
    tot = cur.fetchone()[0]
    print(f"  당사분 외 코드에 0 아닌 금액이 있는 사람: {n}명 / 전체 {tot}명")
    cur.execute(f"""SELECT TOP 20 r.EmpName, s.SMPerCoAllType, SUM(s.Amt)
                    FROM _TWPRAdjTotNtsIncomeSum s
                    JOIN _TWPRAdjTotResult r
                      ON r.YY=s.YY AND r.EmpSeq=s.EmpSeq
                    WHERE s.YY=%s AND s.SMPerCoAllType IN ({ph}) AND s.Amt <> 0
                    GROUP BY r.EmpName, s.SMPerCoAllType
                    ORDER BY r.EmpName""", tuple([args.yy] + other))
    for nm, c, a in cur.fetchall():
        print(f"    {nm:<10} 코드 {c}  {int(float(a or 0)):>14,}")

conn.close()
print("\n① 에서 종전분 코드를 확인하고 ③④ 에서 회사정보 테이블을 확정하면,")
print("build_income_rows() 의 하드코딩된 빈 열을 실제 값으로 채울 수 있습니다.")
