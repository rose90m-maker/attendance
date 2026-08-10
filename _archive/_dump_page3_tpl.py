#!/usr/bin/env python3
"""_dump_page3_tpl.py — 3쪽 서식의 반복 블록 구조 확인 (읽기 전용)

3쪽 신용카드 표의 개인별 행이 여전히 빈다. expand_family_rows() 는
`<!-- Data7_repeat Begin-->` 만 찾는데, 두 번째 표가 다른 마커를 쓰거나
반복 블록이 아예 아닐 수 있다. 서식 원본에서 확인한다.

ERP 는 읽기만 한다.

사용:  python3 _archive/_dump_page3_tpl.py [--yy 2025] [--save page3.html]
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import wht_receipt as W

ap = argparse.ArgumentParser()
ap.add_argument("--yy", default="2025")
ap.add_argument("--save", default="")
args = ap.parse_args()


def head(t):
    print(f"\n{'=' * 66}\n  {t}\n{'=' * 66}")


conn = W._conn()
cur = conn.cursor()
cur.execute("""SELECT Head, Style, Page1, Page2, Page3, Detail, Footer
               FROM _TWPRAdjTotAbrIncomeHTML WHERE YY=%s""", (args.yy,))
r = cur.fetchone()
conn.close()
if not r:
    sys.exit(f"{args.yy} 서식 없음")

parts = dict(zip(("Head", "Style", "Page1", "Page2", "Page3", "Detail", "Footer"),
                 (x or "" for x in r)))

head("1. 서식 섹션 크기")
for k, v in parts.items():
    print(f"   {k:<8}{len(v):>9,}자")

head("2. 전체 서식의 반복 마커")
for k, v in parts.items():
    marks = re.findall(r"<!--\s*([A-Za-z0-9_]+)_repeat\s+Begin\s*-->", v)
    ends = re.findall(r"([A-Za-z0-9_]+)_repeat\s+END\s*-->", v)
    if marks or ends:
        print(f"   [{k}]  Begin={marks}   END={ends}")
if not any(re.search(r"_repeat", v) for v in parts.values()):
    print("   반복 마커가 하나도 없습니다.")

p3 = parts["Page3"]
if args.save:
    open(args.save, "w", encoding="utf-8").write(parts["Style"] + p3)
    print(f"\nPage3 저장: {args.save}")

head("3. Page3 안의 Data7 토큰 분포")
# 신용카드 표에 쓰이는 토큰이 어디쯤 나오는지 위치로 본다
for tok in ("Data7_NtsPlastic", "Data7_SumNtsPlastic", "Data7_NtsInsurance2",
            "Data7_DepenNm", "Data7_DepenType"):
    pos = [m.start() for m in re.finditer(re.escape(tok), p3)]
    print(f"   {tok:<24}{len(pos)}회  위치 {pos[:8]}")

head("4. 반복 마커 위치 (Page3)")
for m in re.finditer(r"<!--\s*[A-Za-z0-9_]+_repeat\s+(?:Begin|END)\s*-->", p3):
    print(f"   {m.start():>7}  {m.group(0)}")

head("5. 신용카드 표 주변 구조")
i = p3.find("신용카드")
if i < 0:
    print("   '신용카드' 문자열이 Page3 에 없습니다.")
else:
    seg = p3[max(0, i - 300): i + 2600]
    seg = re.sub(r"\s+", " ", seg)
    # 토큰과 태그 구조만 남겨 읽기 쉽게
    print("   ── 원문 일부 ──")
    print("   " + seg[:2400])

print("\n위 2·4번이 핵심입니다 — 두 번째 표의 마커 이름을 알려주세요.")
