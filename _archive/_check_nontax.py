#!/usr/bin/env python3
"""_check_nontax.py — Ⅱ 비과세·감면 소득명세와 52/54 세액감면 검증 (읽기 전용)

이재현 2025 발급본의 정답:
    (18)-32 중소기업 취업자에 대한 감면(90%)   T13   21,980,755   21,980,755
    20.비과세소득 계     (비어 있음)
    20-1.감면소득 계                                  21,980,755   21,980,755
    52.「조세특례제한법」제30조   473,817
    54.세액감면 계               473,817

20 / 20-1 의 토큰 이름(Deduc / NonTax)만 봐서는 어느 쪽이 어느 행인지 알 수 없다.
render() 가 서식을 보고 정하므로, 여기서는 그 판단이 맞았는지 행 글자로 확인한다.

ERP 는 읽기만 한다.

사용:  python3 _archive/_check_nontax.py [--yy 2025]
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
ap.add_argument("--name", default="이재현")
args = ap.parse_args()

EXPECT = {                       # 이재현 발급본 실측
    "감면행 표시명": "(18)-32 중소기업 취업자에 대한 감면(90%)",
    "감면코드": "T13",
    "감면금액": "21,980,755",
    "52.조특법 제30조": "473,817",
    "54.세액감면 계": "473,817",
}


def head(t):
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


def row_text(tpl, tok):
    i = tpl.find("YLW#_" + tok)
    if i < 0:
        return ""
    s, e = tpl.rfind("<tr", 0, i), tpl.find("</tr>", i)
    if s < 0 or e < 0:
        return ""
    txt = re.sub(r"<[^>]+>", " ", re.sub(r"YLW#_\w+", " ", tpl[s:e]))
    return re.sub(r"\s+", " ", txt).strip()


conn = W._conn()
cur = conn.cursor()
tpl = W.load_template(cur, args.yy)

head("① 20 / 20-1 토큰이 어느 행인지")
for group, toks in W._SUM_TOKENS.items():
    print(f"  {group:<8}{toks[0]:<24}{row_text(tpl, toks[0])[:44] or '(못 찾음)'}")

cur.execute("""SELECT EmpSeq, EmpID FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
r0 = cur.fetchone()
if not r0:
    sys.exit(f"{args.yy} 귀속에 '{args.name}' 없음")
emp_seq, emp_id = r0[0], str(r0[1]).strip()

head(f"② {args.name} — Ⅱ영역 행")
rows = W.load_nontax(cur, emp_seq, args.yy)
if not rows:
    print("  (해당 항목 없음)")
for r in rows:
    kind = "감면" if r["smtype"] == W.REDUC_SMTYPE else "비과세"
    print(f"  [{kind}] {r['code']:<5}{r['title'][:40]:<42}"
          f"합계 {int(r['amt']):>12,}  종전 {int(r['pre']):>12,}")

head(f"③ {args.name} — 렌더 결과에서 확인")
with contextlib.redirect_stdout(io.StringIO()):
    html, _t, _f, missing = W.render(cur, emp_id, args.yy)
    _t2, v, _d = W.build_values(cur, emp_id, args.yy)
text = re.sub(r"<[^>]+>", " ", html)
text = re.sub(r"\s+", " ", text)

miss = []
checks = [
    ("감면행 표시명", EXPECT["감면행 표시명"] in text),
    ("감면코드 T13", " T13 " in text or ">T13<" in html),
    ("감면금액 21,980,755", "21,980,755" in text),
    ("52.조특법 제30조 473,817", v.get("Data6_Amt56") == "473,817"),
    ("54.세액감면 계 473,817", v.get("Data6_Amt58") == "473,817"),
]
for label, ok in checks:
    print(f"  {label:<32}{'✅' if ok else '❌'}")
    if not ok:
        miss.append(label)

print(f"\n  Data6_Amt56 (52번) = {v.get('Data6_Amt56') or '(빈칸)'}")
print(f"  Data6_Amt58 (54번) = {v.get('Data6_Amt58') or '(빈칸)'}")
for g, toks in W._SUM_TOKENS.items():
    print(f"  {toks[0]:<24}= {v.get(toks[0], '(값 없음)') or '(빈칸)'}"
          f"   ({row_text(tpl, toks[0])[:30]})")

head("④ 회귀 — 감면이 없는 사람은 Ⅱ영역이 비어야 한다")
cur.execute("""SELECT TOP 3 EmpID, EmpName FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName <> %s ORDER BY EmpName""",
            (args.yy, args.name))
for eid, nm in cur.fetchall():
    eid = str(eid).strip()
    cur.execute("""SELECT EmpSeq FROM _TWPRAdjTotResult
                   WHERE YY=%s AND EmpID=%s""", (args.yy, eid))
    es = cur.fetchone()[0]
    rws = W.load_nontax(cur, es, args.yy)
    with contextlib.redirect_stdout(io.StringIO()):
        _h, _t3, _f3, _m3 = W.render(cur, eid, args.yy)
    print(f"  {nm:<10} Ⅱ영역 항목 {len(rws)}개  "
          f"{'✅ 비어 있음' if not rws else '값 있음 — 서식 확인 필요'}")

conn.close()

head("판정")
if miss:
    print(f"  ❌ 발급본과 다른 것 {len(miss)}건")
    for m in miss:
        print(f"      {m}")
else:
    print("  ✅ 이재현 2025 의 Ⅱ영역·52·54 가 발급본과 같습니다.")
print("\n  ①의 행 글자가 20 / 20-1 로 갈렸는지 눈으로도 확인하세요.")

# 실패는 종료코드로 알린다. 그래야 `검증 && 배포` 가 실제로 막힌다.
sys.exit(1 if miss else 0)
