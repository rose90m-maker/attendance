#!/usr/bin/env python3
"""_wht_cells.py — ERP 금액이 '서식의 어느 칸'에 찍히는지 확인 (읽기 전용)

지금까지의 자동검사(wht_watch)는 "그 숫자가 서식 어딘가에 있는가"만 봤다.
칸이 밀려도 통과한다. 발급본 PDF 없이 칸 위치를 확인하려면 반대로 물어야 한다.

    이 ERP 항목의 금액은 → 어느 토큰에 실리고 → 그 토큰은 서식의 어느 행에 있나

토큰이 어느 행에 있는지는 템플릿 HTML 자체가 답을 갖고 있다.
`YLW#_Data6_Amt74` 를 감싼 <tr> 의 글자를 읽으면 "62 의료비" 가 나온다.
그 행 이름이 ERP 항목명과 맞으면 칸이 맞은 것이다.

토큰↔ERP항목 대응은 손으로 적지 않는다. 코드가 바뀌면 표도 따라 틀어지므로,
ERP 값에 항목마다 다른 표식값을 넣고 한 번 더 렌더해서 **어느 토큰이 변했는지**로
역산한다. wht_receipt.py 를 고쳐도 이 스크립트는 저절로 따라온다.

ERP 는 읽기만 한다. 파일도 쓰지 않는다 (--save 를 준 경우만 HTML 저장).

사용:
  python3 _archive/_wht_cells.py --name 김미선
  python3 _archive/_wht_cells.py --name 김미선 --save 김미선.html
  python3 _archive/_wht_cells.py --name 지창구 --yy 2025
"""
import argparse
import contextlib
import io
import os
import re
import sys
from decimal import Decimal

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
ap.add_argument("--save", default="", help="렌더된 HTML 저장 경로")
args = ap.parse_args()

# 표식값 — 실제 금액과 절대 겹치지 않게 큰 수를 쓴다.
MARK_BASE = 987_000_000


def head(t):
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


def _strip(frag):
    frag = re.sub(r"YLW#_\w+", " ", frag)          # 다른 토큰은 지운다
    frag = re.sub(r"<[^>]+>", " ", frag)
    frag = frag.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", frag).strip()


def row_span(tpl, token):
    """토큰을 감싼 <tr> 의 범위. 없으면 (None, None)."""
    i = tpl.find("YLW#_" + token)
    if i < 0:
        return None, None
    s = tpl.rfind("<tr", 0, i)
    e = tpl.find("</tr>", i)
    if s < 0 or e < 0:
        return None, None
    return s, e


def row_text(tpl, token):
    """토큰을 감싼 <tr> 의 글자. 서식에서 그 칸이 속한 행 이름이 나온다."""
    s, e = row_span(tpl, token)
    return None if s is None else _strip(tpl[s:e])


def near_text(tpl, token, back=3):
    """행 글자가 짧아 판단이 안 될 때 — 앞 행 몇 개까지 거슬러 읽는다.

    서식은 한 항목이 여러 행(대상금액/공제금액)으로 쪼개져 있어서, 뒤쪽 행에는
    줄번호가 없고 '공제금액' 같은 말만 있다. 그럴 때 앞 행에 번호가 있다.
    """
    s, _e = row_span(tpl, token)
    if s is None:
        return ""
    for _ in range(back):
        p = tpl.rfind("<tr", 0, s)
        if p < 0:
            break
        s = p
    _, e = row_span(tpl, token)
    return _strip(tpl[s:e])


def line_no(key):
    """매핑 키에서 서식 줄번호를 뽑는다. 21 → 21, '61obj' → 61, '73tax' → 73."""
    m = re.match(r"\d+", str(key))
    return int(m.group(0)) if m else None


def has_line_no(text, n):
    """'24.' '24 .' '24)' 처럼 줄번호가 그 행에 있는가"""
    return bool(re.search(rf"(?<!\d){n}\s*[.．)]", text or ""))


conn = W._conn()
cur = conn.cursor()

cur.execute("""SELECT EmpSeq, EmpID FROM _TWPRAdjTotResult
               WHERE YY=%s AND EmpName=%s""", (args.yy, args.name))
r0 = cur.fetchone()
if not r0:
    sys.exit(f"{args.yy} 귀속에 '{args.name}' 없음")
emp_seq, emp_id = r0[0], str(r0[1]).strip()
print(f"대상: {args.name} · 사번 {emp_id} · {args.yy} 귀속")

# ── 1. ERP 원본 (항목명 → 금액) ──────────────────────────────
cur.execute("""SELECT i.AdjItemName, d.Amt, d.OrgAmt
               FROM _TWPRAdjTotResultDtl d
               LEFT JOIN _TWPRAdjTotItem i
                 ON i.YY=d.YY AND i.AdjItemSeq=d.AdjItemSeq
               WHERE d.YY=%s AND d.EmpSeq=%s""", (args.yy, emp_seq))
erp_raw = {}
for nm, amt, org in cur.fetchall():
    nm = (nm or "").strip()
    try:
        erp_raw[nm] = (int(float(amt or 0)), int(float(org or 0)))
    except (TypeError, ValueError):
        pass

# ── 2. 정상 렌더 ─────────────────────────────────────────────
tpl = W.load_template(cur, args.yy)
with contextlib.redirect_stdout(io.StringIO()):
    _t, base_vals, depen = W.build_values(cur, emp_id, args.yy)
    html, _t2, _f, _m = W.render(cur, emp_id, args.yy)

# ── 3. 표식 렌더 — 토큰↔ERP항목 대응을 역산 ──────────────────
keys = sorted(set(W.ERP_ITEM_NAME.values()), key=str)
mark_of = {k: MARK_BASE + (i + 1) * 1000 for i, k in enumerate(keys)}
_orig_apply = W.apply_erp_result


def _marking_apply(r, erp):
    out = _orig_apply(r, erp)
    for k in keys:
        r[k] = mark_of[k]
    return out


W.apply_erp_result = _marking_apply
try:
    with contextlib.redirect_stdout(io.StringIO()):
        _t3, mark_vals, _d = W.build_values(cur, emp_id, args.yy)
finally:
    W.apply_erp_result = _orig_apply

# 표식값 → 그 값을 실은 토큰들
token_of = {k: [] for k in keys}
rev = {f"{v:,}": k for k, v in mark_of.items()}
rev.update({str(v): k for k, v in mark_of.items()})
for tok, val in mark_vals.items():
    k = rev.get(str(val))
    if k is not None:
        token_of[k].append(tok)

# ── 4. 항목별 표 ─────────────────────────────────────────────
head("ERP 항목 → 서식 칸 (칸 위치 확인)")
print(f"  {'ERP 항목명':<22}{'ERP 금액':>12}  {'토큰':<16} 서식에서 그 칸이 속한 행")
print("  " + "-" * 74)

unplaced, mismatched, ok = [], [], 0
suspect = []          # 줄번호가 그 행에서 안 보이는 칸 — 사람이 볼 것
for nm in sorted(W.ERP_ITEM_NAME, key=lambda x: str(W.ERP_ITEM_NAME[x])):
    key = W.ERP_ITEM_NAME[nm]
    amt = erp_raw.get(nm, (None, None))[0]
    toks = token_of.get(key) or []
    if amt is None:
        continue                                   # 이 직원에게 없는 항목
    if not toks:
        # 토큰이 없다고 곧장 누락은 아니다. 기납부세액처럼 ERP 값이 아니라
        # 급여 집계에서 따로 채우는 칸이 있다 (5번 항목 참고).
        elsewhere = abs(amt) >= 1 and f"{amt:,}" in html
        unplaced.append((nm, amt, elsewhere))
        note = "다른 경로로 출력됨(값 일치)" if elsewhere else "❌ 서식에 실리지 않음"
        print(f"  {nm:<22}{amt:>12,}  {'(토큰 없음)':<16} {note}")
        continue
    n = line_no(key)
    for tok in toks:
        rt = row_text(tpl, tok) or "(템플릿에서 못 찾음)"
        shown = base_vals.get(tok, "")
        flag = "✅" if (amt == 0 or f"{amt:,}" == str(shown)) else "⚠️ "
        if flag == "✅":
            ok += 1
        else:
            mismatched.append((nm, amt, tok, shown))
        # 칸 위치 자동 판정 — 매핑 키가 곧 서식 줄번호이므로 기계가 볼 수 있다.
        mark = ""
        if n is not None and not has_line_no(rt, n):
            wide = near_text(tpl, tok)
            if has_line_no(wide, n):
                mark = f" (줄번호 {n} 은 앞 행에 있음)"
            else:
                mark = f" ⛔ 줄번호 {n} 이 이 행에 없음"
                suspect.append((nm, key, tok, rt, wide))
        print(f"  {nm:<22}{amt:>12,}  {tok:<16} {flag} {rt[:48]}{mark}")

if suspect:
    head("칸 위치가 의심스러운 것 — 원문 확인")
    print("  매핑 키(=서식 줄번호)가 그 행에도, 바로 앞 행들에도 없습니다.")
    print("  서식이 그 번호를 안 쓰는 것일 수도, 칸이 밀린 것일 수도 있습니다.\n")
    for nm, key, tok, rt, wide in suspect:
        print(f"  ▸ {nm}  (줄번호 {line_no(key)} · {tok})")
        print(f"      그 행      : {rt[:150]}")
        print(f"      앞 행 포함 : {wide[:220]}\n")

# ── 5. 매핑에 없는 ERP 항목 ──────────────────────────────────
head("매핑에 없는 ERP 항목 (0 아닌 것만)")
missing_map = [(nm, a) for nm, (a, o) in sorted(erp_raw.items())
               if nm not in W.ERP_ITEM_NAME and abs(a) >= 1000]
if not missing_map:
    print("  없음")
else:
    print("  ERP 가 값을 갖고 있으나 우리가 서식으로 옮기지 않는 항목입니다.")
    print("  대부분 한도계산용 중간값이지만, 발급본에 찍혀 있다면 누락입니다.\n")
    for nm, a in missing_map:
        in_html = f"{a:,}" in html
        print(f"    {nm:<34}{a:>13,}   {'서식에 있음' if in_html else '서식에 없음'}")

# ── 6. 3쪽 부양가족 명세 ─────────────────────────────────────
head("3쪽 78.소득·세액공제 명세 (부양가족별)")
if not depen:
    print("  ERP 에 부양가족 행이 없습니다.")
else:
    # pymssql 은 money/numeric 을 decimal.Decimal 로 준다. int/float 만 세면
    # 금액이 전부 0 으로 보인다 (2026-08-10, 김미선 3쪽이 빈 줄로 나온 원인).
    def _n(v):
        if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
            return None
        try:
            return int(v)
        except (ValueError, TypeError, ArithmeticError):
            return None

    cols = list(depen[0].keys())
    strcols = [c for c in cols
               if any(isinstance(d.get(c), str) and d.get(c).strip() for d in depen)]
    print(f"  행 {len(depen)}개 · 컬럼 {len(cols)}개")
    print(f"  글자 컬럼: {', '.join(strcols) or '(없음)'}\n")

    for idx, d in enumerate(depen, 1):
        nm = ""
        for c in strcols:
            v = str(d.get(c) or "").strip()
            if v and not v.isdigit():
                nm = f"{c}={v}"
                break
        amts = {}
        for k, v in d.items():
            if k in W.SKIP_COLS:
                continue
            iv = _n(v)
            if iv is not None and abs(iv) >= 1000:
                amts[k] = iv
        print(f"  ▸ {idx}행  {nm or '(이름 컬럼 못 찾음)'}  — 0 아닌 금액 {len(amts)}개")
        for k, v in sorted(amts.items(), key=lambda x: -abs(x[1]))[:16]:
            tok = f"Data7_{k}"
            in_tpl = ("YLW#_" + tok) in tpl
            print(f"      {k:<26}{v:>12,}   "
                  f"{'✅ 서식에 있음' if f'{v:,}' in html else '❌ 서식에 없음'}"
                  f"{'' if in_tpl else '   (토큰 %s 이 템플릿에 없음)' % tok}")
        if not amts:
            print("      (이 행은 ERP 에도 금액이 없습니다)")

# ── 7. 판정 ──────────────────────────────────────────────────
head("판정")
print(f"  칸 위치 확인 {ok}건")
if mismatched:
    print(f"  ⚠️  ERP 금액과 칸 값이 다른 것 {len(mismatched)}건")
    for nm, amt, tok, shown in mismatched:
        print(f"      {nm} — ERP {amt:,} / 서식 '{shown}' ({tok})")
_lost = [(nm, a) for nm, a, ok_ in unplaced if not ok_]
_else = [(nm, a) for nm, a, ok_ in unplaced if ok_]
if _else:
    print(f"  ℹ️  ERP 값이 아닌 다른 경로로 채워지는 칸 {len(_else)}건 (값은 일치)")
    for nm, amt in _else:
        print(f"      {nm} ({amt:,})")
if _lost:
    print(f"  ❌ 서식에 실리지 않는 매핑 {len(_lost)}건")
    for nm, amt in _lost:
        print(f"      {nm} ({amt:,})")
if suspect:
    print(f"  ⛔ 칸 위치 확인 필요 {len(suspect)}건 (위 원문 참고)")
if not mismatched and not _lost and not suspect:
    print("  매핑된 항목은 전부 제 칸에 제 금액으로 들어갔습니다.")
print("\n  '서식에서 그 칸이 속한 행' 의 글자가 ERP 항목명과 맞는지 눈으로 확인하세요.")
print("  예) 의료비(대상금액) → 62 의료비 행 이면 정상.")

if args.save:
    with open(args.save, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  HTML 저장: {args.save}")

conn.close()
