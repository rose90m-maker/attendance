#!/usr/bin/env python3
"""_find_form_tokens.py — 서식에서 글자로 행을 찾아 그 행의 토큰을 보여준다

"서식에 이 칸이 있는 건 아는데 토큰 이름을 모른다" 상황용 범용 도구.
장애인전용보장성 줄(2026-08-10, 유재영 78,976 이 갈 곳)이 첫 사용처다.

토큰이 없는 행이면 앞뒤 행의 토큰도 같이 보여준다 (rowspan 구조 대비).

사용:
  python3 _archive/_find_form_tokens.py 장애인전용
  python3 _archive/_find_form_tokens.py 고향사랑 특별재난 --yy 2025
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
ap.add_argument("words", nargs="+", help="서식에서 찾을 글자")
ap.add_argument("--yy", default="2025")
args = ap.parse_args()

conn = W._conn()
cur = conn.cursor()
tpl = W.load_template(cur, args.yy)
conn.close()

rows = []                        # (시작위치, 원문)
for m in re.finditer(r"<tr\b", tpl):
    s = m.start()
    e = tpl.find("</tr>", s)
    if e < 0:
        continue
    rows.append((s, tpl[s:e + 5]))


def strip(frag):
    t = re.sub(r"YLW#_(\w+)", r"«\1»", frag)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t.replace("&nbsp;", " ")).strip()


for word in args.words:
    print(f"\n{'=' * 78}\n  '{word}' 가 든 행\n{'=' * 78}")
    hits = [i for i, (_s, frag) in enumerate(rows) if word in frag]
    if not hits:
        print("  (없음)")
        continue
    for i in hits:
        print(f"\n  ── {i}번째 행 ──")
        print(f"  {strip(rows[i][1])[:220]}")
        toks = re.findall(r"YLW#_(\w+)", rows[i][1])
        if toks:
            print(f"  토큰: {', '.join(toks)}")
        else:
            print("  토큰 없음 → 앞뒤 행:")
            for j in (i - 1, i + 1, i + 2):
                if 0 <= j < len(rows):
                    t2 = re.findall(r"YLW#_(\w+)", rows[j][1])
                    print(f"    [{j}] {strip(rows[j][1])[:120]}")
                    if t2:
                        print(f"        토큰: {', '.join(t2)}")
