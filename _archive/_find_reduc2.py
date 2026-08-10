#!/usr/bin/env python3
"""_find_reduc2.py — 감면소득 명세(Ⅱ)를 채우는 데 필요한 조인 키 확인 (읽기 전용)

_find_reduc.py 로 여기까지 알아냈다.
  · 대상자의 감면 정보는 _TWPRAdjTotNonTaxInfo 에 NtsItemSeq(898/899)로 있다
    SMAdjCurrPreInfo 3249001=주(현) / 3249002=종(전) 로 보인다
  · 감면코드 'T13' 은 _TWPRAdjTotPrintMapping(.Dtl).Remark 에 있다
  · 금액 21,980,755 는 _TWPRAdjTotNtsIncomeSum 의 Amt/PreAmt 에 있다
  · 서식 토큰: 반복블록 Data4_Title/NtsCd/Cur/Pre1~3/TotAmt,
    블록 밖 Data4_DeducSum*(20-1 감면소득 계), Data4_NonTaxSum*(20 비과세 계)

남은 건 하나다 — **NtsItemSeq 를 감면명칭과 코드(T13)에 잇는 키**.
그것만 확인되면 Data4 를 채울 수 있다.

발급본(이재현 2025)의 정답:
    (18)-32 중소기업 취업자에 대한 감면(90%)   T13   21,980,755   21,980,755
    20-1.감면소득 계                                  21,980,755   21,980,755
    (20.비과세소득 계는 비어 있음)

ERP 는 읽기만 한다.

사용:  python3 _archive/_find_reduc2.py [--name 이재현] [--yy 2025]
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


def head(t):
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


def dump(cur, sql, params=(), limit=30):
    try:
        cur.execute(sql, params)
    except Exception as e:
        print(f"   조회 실패: {str(e)[:150]}")
        return
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    print("   " + " | ".join(cols))
    for r in rows[:limit]:
        print("   " + " | ".join(
            (str(x).strip()[:28] if x is not None else "") for x in r))
    if len(rows) > limit:
        print(f"   … 외 {len(rows) - limit}행")


conn = W._conn()
cur = conn.cursor()

cur.execute("""SELECT EmpSeq FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
r0 = cur.fetchone()
if not r0:
    sys.exit(f"{args.yy} 귀속에 '{args.name}' 없음")
emp_seq = r0[0]
print(f"대상: {args.name} · EmpSeq {emp_seq} · {args.yy} 귀속")

head("① NonTaxInfo 의 NtsItemSeq 가 무슨 항목인가")
dump(cur, """SELECT n.NtsItemSeq, n.SMAdjCurrPreInfo, n.UMTaxExemptType,
                    i.NtsItemName
             FROM _TWPRAdjTotNonTaxInfo n
             LEFT JOIN (SELECT DISTINCT YY, NtsItemSeq, NtsItemName
                        FROM _TWPRAdjTotNtsItem) i
               ON i.YY=n.YY AND i.NtsItemSeq=n.NtsItemSeq
             WHERE n.YY=%s AND n.EmpSeq=%s""", (args.yy, emp_seq))

head("② 그 NtsItemSeq 의 금액 (NtsIncomeSum)")
dump(cur, """SELECT s.NtsItemSeq, i.NtsItemName, s.SMPerCoAllType,
                    s.Amt, s.PreAmt
             FROM _TWPRAdjTotNtsIncomeSum s
             LEFT JOIN (SELECT DISTINCT YY, NtsItemSeq, NtsItemName
                        FROM _TWPRAdjTotNtsItem) i
               ON i.YY=s.YY AND i.NtsItemSeq=s.NtsItemSeq
             WHERE s.YY=%s AND s.EmpSeq=%s
               AND s.NtsItemSeq IN (SELECT NtsItemSeq FROM _TWPRAdjTotNonTaxInfo
                                    WHERE YY=s.YY AND EmpSeq=s.EmpSeq)""",
     (args.yy, emp_seq))

head("③ 감면코드가 든 PrintMapping — 전체 컬럼")
for tbl in ("_TWPRAdjTotPrintMapping", "_TWPRAdjTotPrintMappingDtl"):
    print(f"\n  {tbl}")
    dump(cur, f"SELECT * FROM [{tbl}] WHERE RTRIM(Remark) LIKE 'T%'", ())

head("④ 감면 성격의 NtsItem 전체 (이름에 감면/비과세)")
dump(cur, """SELECT DISTINCT NtsItemSeq, NtsItemName FROM _TWPRAdjTotNtsItem
             WHERE YY=%s AND (NtsItemName LIKE '%감면%'
                              OR NtsItemName LIKE '%비과세%')
             ORDER BY NtsItemSeq""", (args.yy,), limit=60)

head("⑤ 이 사람의 NtsIncomeSum 전체 (0 아닌 것)")
dump(cur, """SELECT s.NtsItemSeq, i.NtsItemName, s.Amt, s.PreAmt
             FROM _TWPRAdjTotNtsIncomeSum s
             LEFT JOIN (SELECT DISTINCT YY, NtsItemSeq, NtsItemName
                        FROM _TWPRAdjTotNtsItem) i
               ON i.YY=s.YY AND i.NtsItemSeq=s.NtsItemSeq
             WHERE s.YY=%s AND s.EmpSeq=%s AND (s.Amt<>0 OR s.PreAmt<>0)
             ORDER BY s.NtsItemSeq""", (args.yy, emp_seq), limit=40)

head("⑥ 서식 2쪽 52/54 번 행의 토큰")
tpl = W.load_template(cur, args.yy)
for line, want in ((52, "조세특례제한법"), (54, "세액감면")):
    hit = None
    for m in re.finditer(r"<tr\b", tpl):
        s = m.start()
        e = tpl.find("</tr>", s)
        if e < 0:
            continue
        frag = tpl[s:e]
        plain = re.sub(r"<[^>]+>", " ", re.sub(r"YLW#_\w+", " ", frag))
        if re.search(rf"(?<!\d){line}\s*[.．]", plain) and want in plain:
            toks = re.findall(r"YLW#_(\w+)", frag)
            hit = (re.sub(r"\s+", " ", plain).strip()[:70], toks)
            break
    if hit:
        print(f"  {line}번 행: {hit[0]}")
        print(f"        토큰: {', '.join(hit[1]) or '(없음)'}")
    else:
        print(f"  {line}번 행을 못 찾음")

head("⑦ 우리가 지금 그 칸에 넣는 값 (이재현)")
cur2 = conn.cursor()
cur.execute("""SELECT EmpID FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
emp_id = str(cur.fetchone()[0]).strip()
import contextlib
import io
with contextlib.redirect_stdout(io.StringIO()):
    _t, v, _d = W.build_values(cur2, emp_id, args.yy)
for k in sorted(v):
    if k.startswith("Data4_") or k in ("Data6_Amt56", "Data6_Amt57"):
        print(f"    {k:<26}{str(v[k])[:40] or '(빈칸)'}")

conn.close()
print("\n①②③ 으로 Data4 반복행을, ⑥ 으로 54번 토큰을 확정합니다.")
