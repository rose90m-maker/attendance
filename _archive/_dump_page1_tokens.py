#!/usr/bin/env python3
"""_dump_page1_tokens.py — 1쪽 서식의 토큰을 순서대로 확인 (읽기 전용)

종(전)근무지를 채우려면 어느 칸이 어느 토큰인지 정확히 알아야 한다.
특히 두 가지는 추측하면 안 된다.

  · 16.계 행의 합계 칸 토큰 이름 (Data3_SumCur / Sumpre1~3 외에 총계가 있는가)
  · 하단 영수란 보험료의 두 칸 순서 (Cur 가 먼저인가 Tot 가 먼저인가)

발급본은 「국민연금(현근무지) 1,153,570 ( 1,053,000 )」 처럼 두 값을 찍는데
지금 우리는 두 칸에 같은 값을 넣고 있어 순서를 알 수 없다.

토큰이 템플릿에 나오는 **순서 그대로** 출력하므로, 왼쪽 칸이 위에 온다.

ERP 는 읽기만 한다.

사용:  python3 _archive/_dump_page1_tokens.py [--yy 2025] [--name 김미선]
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
ap.add_argument("--name", default="김미선")
args = ap.parse_args()

PREFIXES = ("Data2_", "Data3_", "Data5_")


def head(t):
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


def strip(frag):
    frag = re.sub(r"YLW#_(\w+)", r"«\1»", frag)
    frag = re.sub(r"<[^>]+>", " ", frag)
    frag = frag.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", frag).strip()


conn = W._conn()
cur = conn.cursor()
tpl = W.load_template(cur, args.yy)

head("1쪽 토큰 — 템플릿에 나오는 순서 (왼쪽 칸이 위)")
print("  «토큰» 은 같은 행에 있는 다른 토큰입니다. 칸 순서를 이걸로 판단하세요.\n")

seen = set()
for m in re.finditer(r"YLW#_(\w+)", tpl):
    tok = m.group(1)
    if not tok.startswith(PREFIXES) or tok in seen:
        continue
    seen.add(tok)
    i = m.start()
    s = tpl.rfind("<tr", 0, i)
    e = tpl.find("</tr>", i)
    row = strip(tpl[s:e]) if s >= 0 and e > 0 else "(행 못 찾음)"
    print(f"  {tok:<26} {row[:96]}")

# 반복블록 표시 — Data2/Data3 는 render() 가 복제하므로 원본에 marker 가 있다
head("반복블록 표식")
for mk in ("Data2_repeat", "Data3_repeat", "Data4_repeat",
           "Data7_repeat", "Data8_repeat"):
    print(f"  {mk:<16}{'있음' if mk in tpl else '없음'}")

# ── 채워지지 않은 토큰 ──────────────────────────────────────
head(f"{args.name} 렌더 시 값이 없어 빈칸으로 나가는 1쪽 토큰")
cur.execute("""SELECT EmpID FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
r0 = cur.fetchone()
if not r0:
    print(f"  ({args.name} 없음 — 건너뜀)")
else:
    emp_id = str(r0[0]).strip()
    with contextlib.redirect_stdout(io.StringIO()):
        html, _t, filled, missing = W.render(cur, emp_id, args.yy)
    miss1 = [k for k in missing if k.startswith(PREFIXES)]
    print(f"  값 없는 토큰 {len(miss1)}개 (전체 미채움 {len(missing)}개)")
    for k in miss1:
        print(f"    {k}")

    head("현재 채워진 1쪽 값 (종전근무지 반영 전)")
    _t2, vals, _d = W.build_values(cur, emp_id, args.yy)
    for k in sorted(vals):
        if k.startswith(("Data3_", "Data5_")) and str(vals[k]).strip() \
                and k not in ("Data5_SealPhoto",):
            print(f"    {k:<26}{str(vals[k])[:40]}")

conn.close()
print("\n이 표로 16.계 합계 칸과 보험료 두 칸의 토큰·순서를 확정한 뒤 고칩니다.")
