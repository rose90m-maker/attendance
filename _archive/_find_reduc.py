#!/usr/bin/env python3
"""_find_reduc.py — 비과세·감면 소득명세(Ⅱ)와 세액감면(52/54) 출처 찾기 (읽기 전용)

이재현 2025 발급본에 있는데 우리 서식이 못 채우는 두 곳이다.

  Ⅱ 비과세소득 및 감면소득명세
      (18)-32 중소기업 취업자에 대한 감면(90%)   T13   21,980,755   21,980,755
      20-1.감면소득 계                                  21,980,755   21,980,755
      → 우리는 Data4 를 빈 행 14개로 채운다. 감면코드·명칭·금액이 전부 빈다.

  52.「조세특례제한법」제30조   473,817
  54.세액감면 계               473,817
      → ERP_ITEM_NAME 에 52/54 가 없다. wht_calc 계산값이 우연히 맞을 뿐이고,
        54 는 토큰이 있는지조차 확인 안 됐다.

찾을 것
  ① 감면코드 'T13' 과 감면명칭이 저장된 곳
  ② 감면소득 금액 21,980,755 가 '감면' 성격으로 저장된 곳
  ③ 52/54 에 해당하는 ERP 항목명 (473,817)
  ④ 서식 Data4 블록의 토큰 이름

ERP 는 읽기만 한다.

사용:  python3 _archive/_find_reduc.py [--name 이재현] [--yy 2025]
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
ap.add_argument("--name", default="이재현")
ap.add_argument("--yy", default="2025")
args = ap.parse_args()

CODE = "T13"
REDUC_AMT = 21980755
TAX_REDUC = 473817


def head(t):
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


conn = W._conn()
cur = conn.cursor()

cur.execute("""SELECT EmpSeq, EmpID FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
r0 = cur.fetchone()
if not r0:
    sys.exit(f"{args.yy} 귀속에 '{args.name}' 없음")
emp_seq, emp_id = r0[0], str(r0[1]).strip()
print(f"대상: {args.name} · EmpSeq {emp_seq} · {args.yy} 귀속")

# ── ① 감면코드 T13 ──────────────────────────────────────────
head(f"① 감면코드 '{CODE}' 가 저장된 곳")
cur.execute("""SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
               WHERE DATA_TYPE IN ('nvarchar','varchar','nchar','char')
                 AND (TABLE_NAME LIKE '%Adj%' OR TABLE_NAME LIKE '%Reduc%'
                      OR TABLE_NAME LIKE '%NonTax%')
                 AND TABLE_NAME NOT LIKE '%Log'""")
found = 0
for tbl, col in cur.fetchall():
    try:
        cur.execute(f"SELECT COUNT(*) FROM [{tbl}] WHERE RTRIM([{col}])=%s",
                    (CODE,))
        n = cur.fetchone()[0]
    except Exception:
        continue
    if n:
        found += 1
        print(f"  ✅ {tbl}.{col}  ({n}행)")
if not found:
    print("  ❌ 못 찾음 — 코드가 숫자 seq 로 저장되고 인쇄할 때만 T13 이 될 수 있다")

# ── ② 감면/비과세 성격 테이블 ───────────────────────────────
head("② 비과세·감면 관련 테이블과 대상자의 행")
cur.execute("""SELECT DISTINCT TABLE_NAME FROM INFORMATION_SCHEMA.COLUMNS
               WHERE COLUMN_NAME='EmpSeq'
                 AND (TABLE_NAME LIKE '%NonTax%' OR TABLE_NAME LIKE '%Reduc%'
                      OR TABLE_NAME LIKE '%Exempt%' OR TABLE_NAME LIKE '%TaxAss%')
                 AND TABLE_NAME NOT LIKE '%Log' AND TABLE_NAME NOT LIKE '%Amd'
               ORDER BY TABLE_NAME""")
tbls = [r[0] for r in cur.fetchall()]
if not tbls:
    print("  (이름으로는 못 찾음)")
for t in tbls:
    try:
        cur.execute(f"SELECT COUNT(*) FROM [{t}] WHERE EmpSeq=%s", (emp_seq,))
        n = cur.fetchone()[0]
    except Exception as e:
        print(f"  {t}: 조회 실패 {str(e)[:70]}")
        continue
    print(f"\n  {t}  — {args.name} 행 {n}개")
    if not n:
        continue
    cur.execute(f"SELECT TOP 4 * FROM [{t}] WHERE EmpSeq=%s", (emp_seq,))
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        for c, v in zip(cols, row):
            s = str(v).strip() if v is not None else ""
            if s and s not in ("0", "0.0000", "0.00000", "0.00"):
                print(f"      {c:<26}{s[:60]}")
        print("      " + "-" * 40)

# ── ③ 21,980,755 를 가진 곳 (감면소득) ──────────────────────
head(f"③ 감면소득 금액 {REDUC_AMT:,} 이 저장된 곳")
cur.execute("""SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
               FROM INFORMATION_SCHEMA.COLUMNS
               WHERE TABLE_NAME LIKE '%Adj%'
                 AND DATA_TYPE IN ('int','bigint','numeric','decimal','money','float')
                 AND TABLE_NAME NOT LIKE '%Log' AND TABLE_NAME NOT LIKE '%Amd'""")
cand = cur.fetchall()
cur.execute("""SELECT DISTINCT TABLE_NAME FROM INFORMATION_SCHEMA.COLUMNS
               WHERE COLUMN_NAME='EmpSeq' AND TABLE_NAME LIKE '%Adj%'""")
emp_tbl = {r[0] for r in cur.fetchall()}
for tbl, col, _d in cand:
    if tbl not in emp_tbl:
        continue
    try:
        cur.execute(f"SELECT COUNT(*) FROM [{tbl}] WHERE EmpSeq=%s AND [{col}]=%s",
                    (emp_seq, REDUC_AMT))
        n = cur.fetchone()[0]
    except Exception:
        continue
    if n:
        print(f"  ✅ {tbl}.{col}  ({n}행)")

# ── ④ 세액감면 473,817 의 ERP 항목명 ────────────────────────
head(f"④ 세액감면 {TAX_REDUC:,} 의 ERP 항목명 (52/54 번 칸)")
cur.execute("""SELECT i.AdjItemName, d.Amt, d.OrgAmt
               FROM _TWPRAdjTotResultDtl d
               LEFT JOIN _TWPRAdjTotItem i
                 ON i.YY=d.YY AND i.AdjItemSeq=d.AdjItemSeq
               WHERE d.YY=%s AND d.EmpSeq=%s
                 AND (d.Amt=%s OR d.OrgAmt=%s)""",
            (args.yy, emp_seq, TAX_REDUC, TAX_REDUC))
rows = cur.fetchall()
if not rows:
    print(f"  ❌ {TAX_REDUC:,} 를 가진 항목이 없음")
for nm, amt, org in rows:
    print(f"  ✅ {(nm or '').strip():<40} Amt {int(float(amt or 0)):>12,} "
          f"OrgAmt {int(float(org or 0)):>12,}")

print("\n  '감면' 이 들어간 항목 전체:")
cur.execute("""SELECT i.AdjItemName, d.Amt
               FROM _TWPRAdjTotResultDtl d
               LEFT JOIN _TWPRAdjTotItem i
                 ON i.YY=d.YY AND i.AdjItemSeq=d.AdjItemSeq
               WHERE d.YY=%s AND d.EmpSeq=%s AND i.AdjItemName LIKE '%감면%'""",
            (args.yy, emp_seq))
for nm, amt in cur.fetchall():
    print(f"      {(nm or '').strip():<40}{int(float(amt or 0)):>12,}")

# ── ⑤ 서식 Data4 토큰 ───────────────────────────────────────
head("⑤ 서식 Ⅱ영역(Data4) 토큰")
tpl = W.load_template(cur, args.yy)
b = tpl.find("<!-- Data4_repeat Begin-->")
e = tpl.find("Data4_repeat END-->")
if b < 0 or e < 0:
    print("  Data4 반복블록을 못 찾음")
else:
    blk = tpl[b:e]
    toks = []
    for m in re.finditer(r"YLW#_(\w+)", blk):
        if m.group(1) not in toks:
            toks.append(m.group(1))
    print(f"  반복블록 안 토큰 {len(toks)}개")
    for t in toks:
        print(f"    {t}")
    txt = re.sub(r"YLW#_\w+", " ", blk)
    txt = re.sub(r"<[^>]+>", " ", txt)
    print("\n  블록의 글자: " + re.sub(r"\s+", " ", txt).strip()[:200])

    # 블록 밖의 Data4 토큰 (20 / 20-1 계 행)
    outside = []
    for m in re.finditer(r"YLW#_(Data4_\w+)", tpl[:b] + tpl[e:]):
        if m.group(1) not in outside:
            outside.append(m.group(1))
    print(f"\n  블록 밖 Data4 토큰 {len(outside)}개: {', '.join(outside)}")

conn.close()
print("\n①③ 으로 감면소득 명세를, ④ 로 52/54 매핑을 채울 수 있습니다.")
