#!/usr/bin/env python3
"""_diff_all_pdf.py — ERP 발급본 PDF 와 우리 서식을 전 직원 한 번에 대조

한 명씩 발급본을 받아 대조하면 결함이 한 번에 하나씩만 나온다.
김미선 한 장에서 3건, 이재현 한 장에서 3건이 더 나왔다. 189명을 그렇게 할 수 없다.

ERP 증명서 발급 화면에서 **전 직원을 한 번에 출력**하면 한 PDF 에 여러 명이
들어간다(파일명 RptWPRAdjTotAbrincomeDtl_...). 그 PDF 를 사람별로 잘라
우리 서식과 숫자를 통째로 맞춰보면, 남은 차이가 한 번에 전부 드러난다.

■ 판정 방법
  발급본과 우리 출력에서 각각 '금액·사업자번호·기간' 을 뽑아 다중집합으로 비교한다.
    · 발급본에만 있는 값 → 우리가 빠뜨린 칸
    · 우리에만 있는 값   → 우리가 잘못 넣은 칸
  칸 위치까지 보려면 _wht_cells.py 를 같이 쓴다. 이건 '빠짐/더함' 을 잡는다.

■ 안 하는 일
  ERP·NAS·DB 를 고치지 않는다. PDF 읽기와 SELECT 뿐이다.

사용:
  python3 _archive/_diff_all_pdf.py 발급본.pdf
  python3 _archive/_diff_all_pdf.py *.pdf --yy 2025
  python3 _archive/_diff_all_pdf.py 발급본.pdf --report wht_diff.txt
"""
import argparse
import contextlib
import io
import os
import re
import sys
from collections import Counter

HERE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import pypdf
except ImportError:
    sys.exit("pypdf 가 없습니다:  pip install pypdf")

import wht_receipt as W

ap = argparse.ArgumentParser()
ap.add_argument("pdfs", nargs="+", help="ERP 발급본 PDF (여러 개 가능)")
ap.add_argument("--yy", default="2025")
ap.add_argument("--report", default="", help="결과를 이 파일에도 저장")
ap.add_argument("--min", type=int, default=1000,
                help="이 금액 미만은 무시 (인원수·코드 등 잡음 제거)")
ap.add_argument("--limit", type=int, default=0, help="앞 N명만 (0=전원)")
args = ap.parse_args()

OUT = []


def say(s=""):
    print(s)
    OUT.append(s)


def head(t):
    say(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


# ── 값 추출 ──────────────────────────────────────────────────
MONEY = re.compile(r"-?\d{1,3}(?:,\d{3})+")
BIZNO = re.compile(r"\d{3}-\d{2}-\d{5}")
PERIOD = re.compile(r"\d{4}\.\d{2}\.\d{2}\s*~\s*\d{4}\.\d{2}\.\d{2}")


def values(text, min_amt):
    """대조 대상 값 — 금액 / 사업자번호 / 기간"""
    c = Counter()
    for m in MONEY.findall(text):
        v = int(m.replace(",", ""))
        if abs(v) >= min_amt:
            c[f"{v:,}"] += 1
    for m in BIZNO.findall(text):
        c[m] += 1
    for m in PERIOD.findall(text):
        c[re.sub(r"\s+", "", m)] += 1
    return c


def html_text(html):
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html,
               flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t.replace("&nbsp;", " "))


# ── 발급본 PDF 를 사람별로 자르기 ────────────────────────────
# 1쪽 머리에 「관리 번호 20251002」 가 있다. 그게 나오면 새 사람이다.
EMPID = re.compile(r"번\s*호\s+(\d{6,})")
FIRSTPAGE = "근로소득 원천징수영수증"


def split_pdfs(paths):
    """PDF 들을 사원별로 자른다. 파일마다 인식 결과를 보고한다.

    파일별 보고가 없으면 '영수증이 아닌 PDF' 와 '1쪽 인식 실패' 를 구분할 수
    없다. 실제로 관계없는 Rpt*.pdf 가 섞여 들어와도 조용히 넘어갔다
    (2026-08-10).
    """
    docs = {}
    order = []
    for p in paths:
        base = os.path.basename(p)
        try:
            rd = pypdf.PdfReader(p)
        except Exception as e:
            say(f"  ⚠️  {base}: 읽기 실패 {type(e).__name__}: {e}")
            continue
        cur_id, found, orphan = None, [], 0
        for page in rd.pages:
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            m = EMPID.search(txt[:600])
            if FIRSTPAGE in txt and m:
                cur_id = m.group(1)
                found.append(cur_id)
                if cur_id not in docs:
                    docs[cur_id] = []
                    order.append(cur_id)
            if cur_id is None:
                orphan += 1
                continue
            docs[cur_id].append(txt)
        n = len(rd.pages)
        if not found:
            say(f"  ⚠️  {base}: {n}쪽 · 영수증을 못 찾음 "
                f"(원천징수영수증 PDF 가 아니거나 텍스트 추출 불가)")
        else:
            note = f" · 머리 못 찾은 앞쪽 {orphan}쪽 무시" if orphan else ""
            say(f"  ✅ {base}: {n}쪽 · {len(set(found))}명{note}")
    return order, docs


say(f"발급본 PDF {len(args.pdfs)}개 읽는 중…")
order, docs = split_pdfs(args.pdfs)
say(f"발급본에서 {len(order)}명 인식")
if not order:
    sys.exit("발급본에서 사람을 못 찾았습니다. "
             "1쪽 머리의 '관리 번호' 가 인식되는지 확인하세요.")
if args.limit:
    order = order[:args.limit]

conn = W._conn()
cur = conn.cursor()
cur.execute("""SELECT EmpID, EmpName FROM _TWPRAdjTotResult WHERE YY=%s""",
            (args.yy,))
NAME = {str(a).strip(): (b or "").strip() for a, b in cur.fetchall()}

head(f"전수 대조 — {len(order)}명 ({args.yy} 귀속)")
say(f"  {'사원':<10}{'쪽':>3}{'빠짐':>6}{'더함':>6}  판정")
say("  " + "-" * 70)

bad, okc, err = [], 0, []
for emp_id in order:
    name = NAME.get(emp_id, "?")
    pages = docs[emp_id]
    erp = values(" ".join(pages), args.min)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            html, _t, _f, _m = W.render(cur, emp_id, args.yy)
    except Exception as e:
        err.append((name, emp_id, f"{type(e).__name__}: {str(e)[:90]}"))
        say(f"  {name:<10}{len(pages):>3}{'':>6}{'':>6}  ❌ 렌더 실패")
        continue
    ours = values(html_text(html), args.min)

    missing = erp - ours          # 발급본에 있는데 우리에 없음
    extra = ours - erp            # 우리에만 있음
    n_m, n_e = sum(missing.values()), sum(extra.values())
    if n_m or n_e:
        bad.append((name, emp_id, missing, extra, pages))
        say(f"  {name:<10}{len(pages):>3}{n_m:>6}{n_e:>6}  ⚠️")
    else:
        okc += 1
        say(f"  {name:<10}{len(pages):>3}{0:>6}{0:>6}  ✅")

# ── 상세 ─────────────────────────────────────────────────────
if bad:
    head("차이 상세")
    for name, emp_id, missing, extra, pages in bad[:40]:
        say(f"\n  ▸ {name} ({emp_id})")
        for v, n in missing.most_common():
            where = next((f"{i}쪽" for i, t in enumerate(pages, 1)
                          if v in t), "?")
            say(f"      발급본에만  {v:>16}  ×{n}   ({where})")
        for v, n in extra.most_common():
            say(f"      우리에만    {v:>16}  ×{n}")
    if len(bad) > 40:
        say(f"\n  … 외 {len(bad) - 40}명")

# ── 공통 패턴 ────────────────────────────────────────────────
if bad:
    head("여러 사람에게 공통으로 빠진 값의 개수")
    say("  같은 칸이 구조적으로 빠졌다면 여기 인원수가 크게 나옵니다.\n")
    common = Counter()
    for _n, _e, missing, _x, _p in bad:
        for v in missing:
            common[v] += 1
    for v, n in common.most_common(25):
        say(f"    {v:>18}   {n}명")

conn.close()

head("요약")
say(f"  대조 {len(order)}명 · 완전일치 {okc}명 · 차이 {len(bad)}명 "
    f"· 렌더실패 {len(err)}명")
for name, emp_id, e in err:
    say(f"      {name}({emp_id}) {e}")
say("""
  읽는 법
    빠짐   발급본에 있는 값이 우리 서식에 없다 → 우리가 빠뜨린 칸
    더함   우리에만 있는 값 → 잘못 넣었거나, 발급본이 0 이라 안 찍은 칸

  이건 '값의 빠짐/더함' 을 본다. 값이 맞는데 **칸이 밀린** 경우는
  _archive/_wht_cells.py 가 본다. 둘을 같이 돌리면 사각지대가 없다.
""")

if args.report:
    with open(args.report, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print(f"\n저장: {args.report}")
