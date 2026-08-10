#!/usr/bin/env python3
"""_find_report_proc.py — ERP 리포트가 호출하는 저장 프로시저 찾기 (읽기 전용)

발급본 PDF 없이 전수 검증하는 길을 찾는다.

ERP 발급본의 파일명은 RptWPRAdjTotAbrincomeDtl 이다. ERP 리포트는 화면이 그리는
게 아니라 저장 프로시저가 돌려준 결과셋을 서식에 얹는 구조라서, 그 프로시저를
찾아 직접 실행하면 **ERP 가 서식에 찍는 값 그 자체**를 전 직원분 얻을 수 있다.
인쇄 단계만 빠진 진짜 정답지다. 그러면 발급본 없이도 189명 전수 대조가 된다.

이 스크립트는 실행하지 않는다 — 찾고, 파라미터와 본문을 보여주기만 한다.
프로시저 중에는 임시테이블·로그를 쓰는 것도 있어서, 본문을 먼저 읽고
SELECT 만 하는지 확인한 뒤에 실행 여부를 정한다.

  ① 이름이 비슷한 프로시저/함수 목록 (AbrIncome, AdjTot + Rpt/Print/Report)
  ② 각각의 파라미터 목록
  ③ 본문 앞부분 (INSERT/UPDATE/DELETE/EXEC 유무 표시)
  ④ 서식 테이블(_TWPRAdjTotAbrIncomeHTML)을 참조하는 모듈 역추적

ERP 는 읽기만 한다 (sys.objects / sys.sql_modules / sys.parameters 조회뿐).

사용:  python3 _archive/_find_report_proc.py [--full 프로시저명]
"""
import argparse
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
ap.add_argument("--full", default="",
                help="이 프로시저의 본문 전체를 출력")
args = ap.parse_args()


def head(t):
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


WRITE_PAT = re.compile(
    r"\b(INSERT\s+INTO(?!\s+#)|UPDATE\s+(?!#)\w|DELETE\s+FROM(?!\s+#)|"
    r"TRUNCATE|MERGE\s+INTO|EXEC(UTE)?\s+\w)", re.I)

conn = W._conn()
cur = conn.cursor()

# ── 본문 전체 출력 모드 ──────────────────────────────────────
if args.full:
    cur.execute("""SELECT m.definition FROM sys.sql_modules m
                   JOIN sys.objects o ON o.object_id=m.object_id
                   WHERE o.name=%s""", (args.full,))
    r = cur.fetchone()
    if not r:
        sys.exit(f"'{args.full}' 없음")
    print(r[0])
    conn.close()
    sys.exit(0)

# ── ① 후보 나열 ─────────────────────────────────────────────
head("① 이름으로 찾은 후보")
cur.execute("""SELECT o.name, o.type_desc, o.create_date, o.modify_date
               FROM sys.objects o
               WHERE o.type IN ('P','FN','IF','TF')
                 AND (o.name LIKE '%AbrIncome%'
                      OR (o.name LIKE '%AdjTot%'
                          AND (o.name LIKE '%Rpt%' OR o.name LIKE '%Print%'
                               OR o.name LIKE '%Report%' OR o.name LIKE '%Query%'
                               OR o.name LIKE '%Sheet%')))
               ORDER BY o.name""")
cands = cur.fetchall()
if not cands:
    print("  (없음 — ④의 역추적 결과를 본다)")
for nm, td, cd, md in cands:
    print(f"  {nm:<52} {td[:12]:<14} 수정 {str(md)[:10]}")

# ── ② 파라미터 + ③ 본문 성격 ──────────────────────────────
head("② 후보별 파라미터와 본문 성격")
for nm, *_ in cands:
    cur.execute("""SELECT p.name, TYPE_NAME(p.user_type_id), p.max_length
                   FROM sys.parameters p
                   JOIN sys.objects o ON o.object_id=p.object_id
                   WHERE o.name=%s ORDER BY p.parameter_id""", (nm,))
    params = cur.fetchall()
    cur.execute("""SELECT m.definition FROM sys.sql_modules m
                   JOIN sys.objects o ON o.object_id=m.object_id
                   WHERE o.name=%s""", (nm,))
    r = cur.fetchone()
    body = (r[0] if r else None) or ""   # 암호화된 모듈은 definition 이 NULL
    writes = sorted({m.group(1).split()[0].upper()
                     for m in WRITE_PAT.finditer(body)})
    n_select = len(re.findall(r"\bSELECT\b", body, re.I))
    print(f"\n  ▸ {nm}  ({len(body):,}자 · SELECT {n_select}회)")
    print(f"    파라미터: " + (", ".join(f"{p} {t}" for p, t, _l in params)
                             or "(없음)"))
    if writes:
        print(f"    ⚠️  쓰기 구문 있음: {', '.join(writes)}"
              f"  (임시테이블(#) 제외하고 검사했는데도 남은 것)")
    else:
        print(f"    ✅ 쓰기 구문 없음 — 실행해도 안전해 보임")

# ── ④ 서식 테이블을 참조하는 모듈 역추적 ────────────────────
head("④ 서식/결과 테이블을 참조하는 모듈")
for target in ("_TWPRAdjTotAbrIncomeHTML", "_TWPRAdjTotEmpDepenList",
               "_TWPRAdjTotPrintMapping"):
    cur.execute("""SELECT DISTINCT o.name, o.type_desc
                   FROM sys.sql_modules m
                   JOIN sys.objects o ON o.object_id=m.object_id
                   WHERE m.definition LIKE %s
                   ORDER BY o.name""", (f"%{target}%",))
    rows = cur.fetchall()
    print(f"\n  {target} 를 참조: {len(rows)}개")
    for nm, td in rows[:15]:
        print(f"    {nm:<52}{td[:14]}")

conn.close()
print("""
다음 단계
  본문이 SELECT 뿐인 후보가 나오면:
    python3 _archive/_find_report_proc.py --full 프로시저명   으로 본문을 확인하고,
  그 프로시저를 전 직원 루프로 실행해 우리 서식과 대조하는 스크립트를 만든다.
  그러면 발급본 PDF 없이 189명 전수 검증이 된다.""")
