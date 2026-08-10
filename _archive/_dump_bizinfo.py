#!/usr/bin/env python3
"""_dump_bizinfo.py — 종(전)근무지 금액이 어디 있는지 확정 (읽기 전용)

_TWPRAdjTotPreWork 에는 회사명·사업자번호·근무기간만 있고 금액이 없다.
발급본(김미선 2025)에는 종(전) 열에 급여 827,000 이 찍히고, 하단 영수란은
바깥값(종전 포함)과 괄호값(현근무지분)이 다르다.

    국민연금 1,153,570 (1,053,000)   차이 100,570
    건강보험 1,079,600 (  936,900)   차이 142,700
    고용보험   203,790 (  196,350)   차이   7,440  = 827,000 × 0.9%

고용보험 차이가 종전 급여의 요율과 정확히 맞으므로 괄호가 현근무지분이다.
그러면 종전 금액이 ERP 어딘가에 있다는 뜻이다.

_TWPRAdjTotBizInfo.AdjBizInfo 에 '301-81-46359' 문자열이 들어 있었다.
_TWPRAdjTotEmpInfo + _TWPRAdjTotEmpInfoMapping (FieldName 으로 푸는 구조)와
같은 방식일 가능성이 높다. 그 매핑을 찾아 필드명으로 풀어본다.

ERP 는 읽기만 한다.

사용:  python3 _archive/_dump_bizinfo.py [--name 김미선] [--yy 2025]
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

# 발급본에서 읽은 종(전) 관련 숫자 — 이 값들이 나오는 곳을 찾는다
WANT = {
    "827000": "종전 급여",
    "100570": "종전 국민연금(추정)",
    "142700": "종전 건강보험(추정)",
    "7440": "종전 고용보험(추정)",
    "20219760": "주(현) 급여",
    "22346350": "주(현) 계",
    "1053000": "현근무지 국민연금",
    "936900": "현근무지 건강보험",
    "196350": "현근무지 고용보험",
}


def head(t):
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


conn = W._conn()
cur = conn.cursor()

cur.execute("""SELECT EmpSeq FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
r0 = cur.fetchone()
if not r0:
    sys.exit(f"{args.yy} 귀속에 '{args.name}' 없음")
emp_seq = r0[0]
print(f"대상: {args.name} · EmpSeq {emp_seq} · {args.yy} 귀속")

# ── ① BizInfo 계열 테이블 구조 ──────────────────────────────
head("① _TWPRAdjTotBizInfo 계열")
cur.execute("""SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
               WHERE TABLE_NAME LIKE '%AdjTotBizInfo%'
                  OR TABLE_NAME LIKE '%AdjTotPreWork%'
               ORDER BY TABLE_NAME""")
tables = [r[0] for r in cur.fetchall()]
for t in tables:
    cur.execute("""SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_NAME=%s ORDER BY ORDINAL_POSITION""", (t,))
    cols = cur.fetchall()
    print(f"\n  {t}")
    print("     " + ", ".join(f"{c}({d})" for c, d in cols))

# ── ② BizInfo 원본 행 ───────────────────────────────────────
head("② _TWPRAdjTotBizInfo 의 원본 행")
try:
    cur.execute("""SELECT * FROM _TWPRAdjTotBizInfo WHERE YY=%s""", (args.yy,))
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    print(f"  {args.yy} 귀속 전체 {len(rows)}행 · 컬럼 {cols}")
    for row in rows[:6]:
        print("  " + "-" * 60)
        for c, v in zip(cols, row):
            s = str(v).strip() if v is not None else ""
            if s:
                print(f"    {c:<24}{s[:300]}")
except Exception as e:
    print(f"  조회 실패: {str(e)[:200]}")

# ── ③ BizInfo 매핑 (필드명) ─────────────────────────────────
head("③ BizInfo 매핑 테이블 — 필드명으로 풀기")
mapped = False
for mtbl in ("_TWPRAdjTotBizInfoMapping", "_TWPRAdjTotBizInfoMap",
             "_TWPRAdjTotEmpInfoMapping"):
    try:
        cur.execute(f"SELECT TOP 60 * FROM [{mtbl}]")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except Exception:
        continue
    print(f"\n  {mtbl}  ({len(rows)}행 표시)")
    print("     " + " | ".join(cols))
    for r in rows:
        print("     " + " | ".join(
            (str(x).strip()[:24] if x is not None else "") for x in r))
    mapped = True
    break
if not mapped:
    print("  (매핑 테이블 없음 — AdjBizInfo 는 통짜 문자열일 수 있다)")

# ── ④ 원하는 숫자가 어느 테이블·컬럼에 있는지 전수 탐색 ─────
head("④ 발급본 숫자가 저장된 곳 찾기 (AdjTot 계열 전수)")
cur.execute("""SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
               FROM INFORMATION_SCHEMA.COLUMNS
               WHERE TABLE_NAME LIKE '%AdjTot%'
                 AND DATA_TYPE IN ('int','bigint','numeric','decimal','money',
                                   'smallint','float','nvarchar','varchar')
               ORDER BY TABLE_NAME""")
allcols = cur.fetchall()

# EmpSeq 를 가진 테이블만 (직원별 데이터)
cur.execute("""SELECT TABLE_NAME FROM INFORMATION_SCHEMA.COLUMNS
               WHERE COLUMN_NAME='EmpSeq' AND TABLE_NAME LIKE '%AdjTot%'""")
emp_tables = {r[0] for r in cur.fetchall()}

hits = {k: [] for k in WANT}
checked = 0
for tbl, col, dtype in allcols:
    if tbl not in emp_tables:
        continue
    if tbl.endswith("Log") or tbl.endswith("Amd"):
        continue
    for val, label in WANT.items():
        try:
            if dtype in ("nvarchar", "varchar"):
                cur.execute(f"SELECT COUNT(*) FROM [{tbl}] "
                            f"WHERE EmpSeq=%s AND [{col}]=%s", (emp_seq, val))
            else:
                cur.execute(f"SELECT COUNT(*) FROM [{tbl}] "
                            f"WHERE EmpSeq=%s AND [{col}]=%s",
                            (emp_seq, int(val)))
            n = cur.fetchone()[0]
        except Exception:
            continue
        checked += 1
        if n:
            hits[val].append(f"{tbl}.{col}")

for val, label in WANT.items():
    where = hits[val]
    if where:
        print(f"  ✅ {label:<22}{int(val):>12,}   {', '.join(where[:4])}")
    else:
        print(f"  ❌ {label:<22}{int(val):>12,}   못 찾음")
print(f"\n  (컬럼 {checked}개 조회)")

# ── ⑤ PreWork 전체 ──────────────────────────────────────────
head("⑤ _TWPRAdjTotPreWork — 2025 귀속 전원")
cur.execute("""SELECT p.EmpSeq, r.EmpName, p.Seq, p.PreCompanyName, p.PreTaxNo,
                      p.WorkBegDate, p.WorkEndDate
               FROM _TWPRAdjTotPreWork p
               LEFT JOIN _TWPRAdjTotResult r
                 ON r.YY=p.YY AND r.EmpSeq=p.EmpSeq
               WHERE p.YY=%s ORDER BY r.EmpName, p.Seq""", (args.yy,))
rows = cur.fetchall()
print(f"  종전근무지가 있는 행 {len(rows)}개")
for es, nm, seq, conm, tno, bd, ed in rows:
    print(f"    {(nm or '?'):<10} #{seq}  {(conm or ''):<22} {tno}  {bd}~{ed}")

conn.close()
print("\n④ 에서 827,000 과 괄호값들이 어느 테이블에 있는지 확인되면,")
print("build_income_rows() 와 Data5 의 종(전)/현근무지 칸을 채울 수 있습니다.")
