#!/usr/bin/env python3
"""_diff_efile.py — 국세청 전자신고 레코드와 우리 서식을 대조 (읽기 전용)

_TWPRAdjTotRecordResultFile.FileText 에는 ERP 가 실제 국세청에 제출한
전산매체 고정폭 레코드가 통째로 있다 (2025 귀속 49명 · 136행 · 2026-03-06 생성).
**법적으로 제출된 값 그 자체**라 발급본 PDF 보다 강한 정답지다.

레이아웃은 하드코딩하지 않는다 — _TWPRAdjTotRecordItem 이 연도·레코드별로
필드명(RecordName)·길이(Lenth)·누적위치(AccrueLenth)를 전부 정의하므로
그 표를 읽어 파싱한다. 서식이 바뀌어도 표가 따라오므로 이 대조기는 안 늙는다.

⚠️ FileText 에는 주민등록번호가 평문으로 있다. 이 스크립트의 **모든 출력은
   마스킹을 거친다** — 주민번호 뒷자리는 절대 화면에 내지 않는다.

고정폭은 바이트 단위(EUC-KR/CP949)로 센다 — 한글 성명이 섞이므로 글자 수로
자르면 그 뒤 필드가 전부 밀린다.

사용:
  python3 _archive/_diff_efile.py                  스키마 확인 + 대조
  python3 _archive/_diff_efile.py --schema-only    스키마와 표본만 (대조 안 함)
  python3 _archive/_diff_efile.py --yy 2025 --min 1000
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

import wht_receipt as W

ap = argparse.ArgumentParser()
ap.add_argument("--yy", default="2025")
ap.add_argument("--min", type=int, default=1000)
ap.add_argument("--schema-only", action="store_true")
# 3305002 = 근로소득 지급명세서 (A~K 10종 · 826필드 · 2,010B) — 진짜 정답지.
# 3305005 = 의료비지급명세서 — 2026-08-10 에 이걸 근로소득으로 착각해 49명
# "전원 차이"가 났었다. 자료구분이 다르면 대조 자체가 무의미하다.
ap.add_argument("--ptype", default="3305002",
                help="대조할 전산매체 종류 (SMPrintType). 0 이면 전체")
# ERP 가 레코드를 DB 에 안 남기고 파일로만 내려주는 경우 — 생성된 파일을
# ~/attendance 에 두고 --file 로 지정하면 그 파일을 정답지로 쓴다.
# ⚠️ 파일에 전 직원 주민등록번호가 평문으로 있다. 채팅·메일로 옮기지 말 것.
ap.add_argument("--file", default="", help="전자신고 파일 경로 (DB 대신 사용)")
args = ap.parse_args()

FILE_TBL = "_TWPRAdjTotRecordResultFile"
ITEM_TBL = "_TWPRAdjTotRecordItem"


def head(t):
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


def mask(s):
    """주민등록번호를 어떤 형태든 가린다"""
    s = str(s)
    s = re.sub(r"(\d{6})[-‐]?(\d{7})", lambda m: m.group(1) + "-" + "*" * 7, s)
    return s


def cols_of(cur, tbl):
    cur.execute("""SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_NAME=%s ORDER BY ORDINAL_POSITION""", (tbl,))
    return cur.fetchall()


conn = W._conn()
cur = conn.cursor()

# ── 0. 스키마 확인 ───────────────────────────────────────────
head("0. 스키마")
icols = cols_of(cur, ITEM_TBL)
print(f"  {ITEM_TBL}: " + ", ".join(f"{c}({d})" for c, d in icols))
inames = [c for c, _d in icols]

text_col = "FileText"
if args.file:
    # ── 1-파일. 생성된 전자신고 파일을 직접 읽는다 ──────────
    raw = open(os.path.expanduser(args.file), "rb").read()
    # 국세청 전산매체는 CP949 고정폭. 줄바꿈이 있으면 줄로, 없으면 레이아웃
    # 총길이로 자른다 (아래에서 레이아웃을 읽은 뒤 처리).
    frows = [{"FileText": ln.decode("cp949", errors="replace")}
             for ln in re.split(rb"\r\n|\n|\r", raw) if ln.strip()]
    fnames = ["FileText"]
    print(f"\n  파일 {args.file}: {len(raw):,}바이트 · {len(frows)}줄")
    if frows:
        print(f"  첫 줄(마스킹): {mask(frows[0]['FileText'][:100])!r}")
else:
    fcols = cols_of(cur, FILE_TBL)
    print(f"  {FILE_TBL}: " + ", ".join(f"{c}({d})" for c, d in fcols))
    fnames = [c for c, _d in fcols]

    # ── 1. 레코드 행 읽기 ────────────────────────────────────
    cur.execute(f"SELECT * FROM [{FILE_TBL}] WHERE YY=%s", (args.yy,))
    frows = [dict(zip(fnames, r)) for r in cur.fetchall()]
    print(f"\n  {args.yy} 귀속 레코드 {len(frows)}행 (전체)")

    # 자료구분 필터 — 파일행에 SMPrintType 류 컬럼이 있으면 여기서 거른다
    ptype_col = next((c for c in fnames if "printtype" in c.lower()), None)
    if args.ptype and args.ptype != "0" and ptype_col:
        before = len(frows)
        frows = [r for r in frows if str(r.get(ptype_col) or "") == args.ptype]
        print(f"  {ptype_col}={args.ptype} 필터: {before} → {len(frows)}행")
if not frows:
    sys.exit(f"""대조할 레코드가 없습니다 (자료구분 {args.ptype}).

ERP 「연말정산_처리/신고」 화면의 정산신고 [파일생성] 을 실행한 뒤:
  · DB 에 쌓였으면 이 스크립트를 그대로 다시,
  · 파일로만 떨어졌으면 그 파일을 ~/attendance 에 두고
      python3 _archive/_diff_efile.py --file 파일명
    로 다시 돌리면 됩니다.""")

text_col = next((c for c in fnames if "text" in c.lower()), None)
if not text_col:
    sys.exit("FileText 류 컬럼을 못 찾음")

# 표본 (마스킹해서 앞부분만)
sample = str(frows[0][text_col] or "")
print(f"  표본({text_col}, 마스킹): {mask(sample[:120])!r}")
print(f"  표본 줄길이: 문자 {len(sample)} · CP949 바이트 "
      f"{len(sample.encode('cp949', errors='replace'))}")

# 레코드 구분 — 파일행 쪽에 타입 컬럼이 있으면 쓰고, 없으면 첫 글자
type_col = next((c for c in fnames
                 if re.search(r"record|gubun|type", c, re.I)
                 and c.lower() not in ("filetext",)), None)
print(f"  레코드 구분 컬럼: {type_col or '(없음 — FileText 첫 글자 사용)'}")

if args.schema_only:
    for fr in frows[:3]:
        print("  --- 행 표본 ---")
        for k, v in fr.items():
            if k == text_col:
                v = mask(str(v)[:100])
            print(f"    {k:<20}{mask(str(v))[:80]}")
    conn.close()
    sys.exit(0)

# ── 2. 레이아웃 읽기 ─────────────────────────────────────────
cur.execute(f"SELECT * FROM [{ITEM_TBL}] WHERE YY=%s", (args.yy,))
irows = [dict(zip(inames, r)) for r in cur.fetchall()]
if not irows:
    sys.exit(f"{ITEM_TBL} 에 {args.yy} 레이아웃 없음")

len_col = next((c for c in inames if c.lower() in ("lenth", "length", "len")), None)
acc_col = next((c for c in inames if "accrue" in c.lower()), None)
name_col = next((c for c in inames if "recordname" in c.lower()), None)
itype_col = next((c for c in inames
                  if re.search(r"printtype|recordtype|gubun|smtype", c, re.I)), None)
seq_col = next((c for c in inames if c.lower() in ("serl", "seq", "recordseq",
                                                   "orderseq", "sortseq")), None)
print(f"\n  레이아웃 컬럼 매핑: 길이={len_col} 누적={acc_col} 이름={name_col} "
      f"타입={itype_col} 순서={seq_col}")
if not (len_col and acc_col and name_col):
    sys.exit("레이아웃 필수 컬럼을 못 찾음 — --schema-only 로 확인 필요")

# 타입별 레이아웃 — 자료구분(SMPrintType) + 레코드종류(A~K) 두 단계일 수 있다.
# 타입성 컬럼을 최대 두 개까지 묶어 그룹 키로 쓴다.
tcols = [c for c in inames
         if re.search(r"printtype|recordtype|rectype|gubun|smtype", c, re.I)][:2]
if args.ptype and args.ptype != "0":
    pcol = next((c for c in tcols if "printtype" in c.lower()), None)
    if pcol:
        before = len(irows)
        irows = [r for r in irows if str(r.get(pcol) or "") == args.ptype]
        print(f"  레이아웃 {pcol}={args.ptype} 필터: {before} → {len(irows)}필드")

layouts = {}
for ir in irows:
    key = tuple(ir.get(c) for c in tcols) if tcols else "ALL"
    layouts.setdefault(key, []).append(ir)
for k in layouts:
    layouts[k].sort(key=lambda x: (int(x[acc_col] or 0)))
# 파일 모드에서 줄바꿈 없는 통짜 매체면 레이아웃 총길이로 자른다.
# (레코드 길이가 전 종류 동일한 형식이 흔하다 — 3305002 는 2,010B)
if args.file and len(frows) <= 2:
    tots = {int(v[-1][acc_col] or 0) for v in layouts.values() if v}
    blob = "".join(fr["FileText"] for fr in frows).encode("cp949",
                                                          errors="replace")
    for tot in sorted(tots, reverse=True):
        if tot > 0 and len(blob) % tot == 0:
            frows = [{"FileText": blob[i:i + tot].decode("cp949",
                                                         errors="replace")}
                     for i in range(0, len(blob), tot)]
            print(f"  줄바꿈 없는 통짜 파일 → {tot}B 단위로 {len(frows)}레코드 분할")
            break

print(f"  레이아웃 종류: {len(layouts)}개 — " +
      ", ".join(f"{k}({len(v)}필드, 총 {int(v[-1][acc_col] or 0)}B)"
                for k, v in list(layouts.items())[:8]))


def parse_line(text, layout):
    """고정폭 한 줄을 CP949 바이트 기준으로 필드 dict 로"""
    b = str(text or "").encode("cp949", errors="replace")
    out = []
    for f in layout:
        ln = int(f[len_col] or 0)
        end = int(f[acc_col] or 0)
        start = end - ln
        if start < 0 or ln <= 0:
            continue
        raw = b[start:end].decode("cp949", errors="replace").strip()
        out.append(((f[name_col] or "").strip(), raw))
    return out


def pick_layout(fr):
    """이 행에 맞는 레이아웃.

    우선순위: ① 파일행의 타입 컬럼이 그룹 키에 들어 있는 것
             ② FileText 첫 글자(A~K 레코드 구분)가 키에 있는 것
             ③ 줄 바이트수 == 레이아웃 총길이
             ④ 길이가 가장 가까운 것
    """
    text = str(fr[text_col] or "")
    b = len(text.encode("cp949", errors="replace"))
    ft = fr.get(type_col) if type_col else None
    first = text[:1]
    cands = list(layouts.items())
    for k, lay in cands:                       # ①②
        key_vals = {str(x) for x in (k if isinstance(k, tuple) else (k,))}
        if (ft is not None and str(ft) in key_vals) or \
           (first and first in key_vals):
            return lay
    for k, lay in cands:                       # ③
        if int(lay[-1][acc_col] or 0) == b:
            return lay
    return min(cands, key=lambda kv:           # ④
               abs(int(kv[1][-1][acc_col] or 0) - b))[1] if cands else []


# ── 3. 사람별로 묶기 ─────────────────────────────────────────
emp_col = next((c for c in fnames if c.lower() == "empseq"), None)
cur.execute("""SELECT EmpSeq, EmpID, EmpName FROM _TWPRAdjTotResult
               WHERE YY=%s""", (args.yy,))
EMP = {r[0]: (str(r[1]).strip(), (r[2] or "").strip()) for r in cur.fetchall()}
NAME2SEQ = {}
for s_, (i_, n_) in EMP.items():
    NAME2SEQ.setdefault(n_, []).append(s_)

people = {}
unassigned = 0
for fr in frows:
    es = fr.get(emp_col) if emp_col else None
    if es is None:
        # 성명 필드로 찾기
        fields = parse_line(fr[text_col], pick_layout(fr))
        nm = next((v for k, v in fields if "성명" in k or "성 명" in k), "")
        cand = NAME2SEQ.get(nm, [])
        es = cand[0] if len(cand) == 1 else None
    if es is None or es not in EMP:
        unassigned += 1
        continue
    people.setdefault(es, []).append(fr)

head(f"대조 — 신고 레코드가 있는 {len(people)}명 "
     f"(귀속 {args.yy} · 미배정 {unassigned}행)")

# ── 4. 값 대조 ───────────────────────────────────────────────
AMT = re.compile(r"^\d{4,}$")

# 금액이 아닌 필드 — 이름으로 거른다 (2026-08-10 1차 실행에서 전원 공통 노이즈).
#   식별번호(주민·사업자·기관), 날짜·연월, 코드·순번,
#   기부 건별 명세(부속명세 영역 — 영수증 서식엔 원래 없다)
SKIP_NAME = re.compile(
    r"주민|등록번호|사업자|생년|연월|일자|년도|연도|날짜|코드|전화|연락처|"
    r"순번|일련|건수|페이지|세무프로그램|제출|이월")


def norm_filed(v):
    """신고 매체 금액 → int. 매체는 음수를 절대값으로 싣기도 하므로
    대조는 절대값 기준으로 한다 (차감징수 환급액이 부호만 다르게 잡히던 문제)."""
    return abs(int(v))


bad, okc = [], 0
covered_fields = Counter()
for es, rows_ in sorted(people.items(), key=lambda x: EMP[x[0]][1]):
    emp_id, name = EMP[es]
    # 신고 레코드에서 금액 뽑기 (필드명과 함께)
    filed = {}
    for fr in rows_:
        for k, v in parse_line(fr[text_col], pick_layout(fr)):
            if SKIP_NAME.search(k or ""):
                continue
            d = v.lstrip("-").lstrip("0") or "0"
            if AMT.match(v.lstrip("-")) and int(d) >= args.min:
                filed.setdefault(norm_filed(int(v)), set()).add(k or "?")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            html, _t, _f, _m = W.render(cur, emp_id, args.yy)
    except Exception as e:
        bad.append((name, emp_id, [("렌더 실패", str(e)[:80])]))
        continue
    text = re.sub(r"<[^>]+>", " ", html)
    ours = {abs(int(m.replace(",", "")))
            for m in re.findall(r"-?\d{1,3}(?:,\d{3})+", text)}
    ours |= {int(m) for m in re.findall(r"(?<![\d,])\d{4,9}(?![\d,])", text)}

    missing = [(v, filed[v]) for v in filed if v not in ours]
    for v, ks in filed.items():
        covered_fields.update(ks)
    if missing:
        bad.append((name, emp_id,
                    [(", ".join(sorted(ks))[:44], f"{v:,}") for v, ks in
                     sorted(missing, key=lambda x: -x[0])]))
        print(f"  {name:<10} 신고금액 {len(filed):>3}종 · 서식에 없음 {len(missing):>2}  ⚠️")
    else:
        okc += 1
        print(f"  {name:<10} 신고금액 {len(filed):>3}종 · 전부 서식에 있음  ✅")

if bad:
    head("차이 상세 (신고에는 있는데 우리 서식에 없는 금액)")
    for name, emp_id, items in bad[:30]:
        print(f"\n  ▸ {name} ({emp_id})")
        for k, v in items[:12]:
            print(f"      {k:<46}{v:>14}")

conn.close()

head("요약")
print(f"  신고 레코드 보유 {len(people)}명 · 완전일치 {okc}명 · 차이 {len(bad)}명")
print(f"  대조에 쓰인 신고 필드 {len(covered_fields)}종")
print("""
  이 대조의 정답지는 국세청에 실제 제출된 전산매체 레코드다.
  나머지 인원은 ERP 에서 전자신고 파일을 전 직원분 재생성하면
  같은 스크립트로 전수 검증이 된다.""")
sys.exit(1 if bad else 0)
