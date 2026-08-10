#!/usr/bin/env python3
"""_check_wht_page3.py — 영수증을 만들어 ERP 발급본과 바로 대조 (읽기 전용)

웹 화면에서 발급 → PDF 저장 → 눈으로 대조 하던 것을 한 번에 끝낸다.
wht_receipt.render() 를 직접 불러 HTML 을 만들고(브라우저·PDF 불필요),
그 안의 금액을 ERP 발급본 값과 맞춰본다.

ERP 는 읽기만 하고 아무것도 쓰지 않는다.

사용:
  python3 _archive/_check_wht_page3.py               지창구 2025
  python3 _archive/_check_wht_page3.py --name 홍길동 --yy 2024
  python3 _archive/_check_wht_page3.py --save out.html   HTML 도 저장
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
ap.add_argument("--name", default="지창구")
ap.add_argument("--yy", default="2025")
ap.add_argument("--save", default="")
args = ap.parse_args()

# ERP 발급본(RptWPRAdjTotAbrincomeDtl_2025.pdf)에서 읽은 값.
# 다른 사람/연도는 기준이 없어 대조를 건너뛴다.
ERP_EXPECT = {
    ("지창구", "2025"): {
        "1·2쪽": [
            (60_620_770, "21.총급여"),
            (12_781_038, "22.근로소득공제"),
            (47_839_732, "23.근로소득금액"),
            (31_657_791, "48.과세표준"),
            (3_488_668,  "49.산출세액"),
            (2_115_426,  "73.결정세액"),
            (-1_017_740, "77.차감징수세액"),
            (2_347_130,  "건강보험(영수란)"),
            (2_509_860,  "국민연금(영수란)"),
            (512_750,    "고용보험(영수란)"),
        ],
        "3쪽 상단 표": [
            (5_461_938, "국세청 계 보장성"),
            (3_823_080, "국세청 계 의료비"),
            (780_860,   "김채진 의료비"),
        ],
        "3쪽 신용카드 표": [
            (45_356_285, "국세청 계 신용카드"),
            (43_017_645, "지창구 신용카드"),
            (2_338_640,  "김채진 신용카드"),
            (860_370,    "지창구 현금영수증"),
            (352_195,    "김채진 현금영수증"),
            (764_980,    "김채진 직불카드"),
            (375_350,    "지창구 전통시장"),
            (448_200,    "김채진 전통시장"),
            (275_750,    "지창구 대중교통"),
            (8_450,      "김채진 대중교통"),
            (1_212_565,  "계 대중교통"),
            (112_445,    "문화체육"),
            (284_200,    "계 전통시장"),
            (823_550,    "계 현금영수증"),
            (866_000,    "기부금"),
        ],
    },
}


def head(t):
    print(f"\n{'=' * 66}\n  {t}\n{'=' * 66}")


conn = W._conn()
cur = conn.cursor()
try:
    cur.execute("""SELECT EmpID FROM _TWPRAdjTotResult
                   WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
    r = cur.fetchone()
    if not r:
        sys.exit(f"{args.yy} 귀속에 '{args.name}' 없음")
    emp_no = str(r[0]).strip()
    print(f"대상: {args.name} · 사번 {emp_no} · {args.yy} 귀속")
    html, t, filled, missing = W.render(cur, emp_no, args.yy)
finally:
    conn.close()

if args.save:
    open(args.save, "w", encoding="utf-8").write(html)
    print(f"HTML 저장: {args.save}")

# HTML 태그를 걷어내고 금액만 뽑는다
text = re.sub(r"<[^>]+>", " ", html)
got = set()
for m in re.findall(r"-?\d{1,3}(?:,\d{3})+", text):
    got.add(int(m.replace(",", "")))

expect = ERP_EXPECT.get((args.name, args.yy))
if not expect:
    head("ERP 기준값 없음")
    print(f"  {args.name} {args.yy} 는 대조 기준이 없습니다.")
    print(f"  생성된 금액 {len(got)}종 — ERP 발급본과 눈으로 대조하세요.")
else:
    total = ok = 0
    for section, items in expect.items():
        head(section)
        for val, label in items:
            total += 1
            hit = val in got
            ok += hit
            print(f"  {'✅' if hit else '❌'} {label:<22}{val:>14,}")
    head("결과")
    print(f"  {ok}/{total} 일치")
    extra = sorted(v for v in got
                   if v not in {x for items in expect.values() for x, _ in items}
                   and abs(v) >= 1000)
    if extra:
        print(f"\n  참고 — 기준목록에 없는 금액 {len(extra)}종 (서식상 정상일 수 있음):")
        print("   " + ", ".join(f"{v:,}" for v in extra[:20]))
    if ok == total:
        print("\n  ERP 발급본과 동일합니다.")
    else:
        print("\n  ❌ 표시된 항목이 서식에 안 나옵니다. 배포가 반영됐는지 먼저 확인하세요.")

if missing:
    head(f"값이 없어 빈칸으로 나간 토큰 {len(missing)}개")
    for i in range(0, min(len(missing), 60), 3):
        print("   " + "  ".join(f"{x:<30}" for x in missing[i:i + 3]))
    if len(missing) > 60:
        print(f"   … 외 {len(missing)-60}개")
