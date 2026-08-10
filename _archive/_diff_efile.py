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
fcols = cols_of(cur, FILE_TBL)
icols = cols_of(cur, ITEM_TBL)
print(f"  {FILE_TBL}: " + ", ".join(f"{c}({d})" for c, d in fcols))
print(f"  {ITEM_TBL}: " + ", ".join(f"{c}({d})" for c, d in icols))
fnames = [c for c, _d in fcols]
inames = [c for c, _d in icols]

# ── 1. 레코드 행 읽기 ────────────────────────────────────────
cur.execute(f"SELECT * FROM [{FILE_TBL}] WHERE YY=%s", (args.yy,))
frows = [dict(zip(fnames, r)) for r in cur.fetchall()]
print(f"\n  {args.yy} 귀속 레코드 {len(frows)}행")
if not frows:
    sys.exit("레코드가 없습니다.")

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

# 타입별 레이아웃 (타입 컬럼 없으면 전체 하나)
layouts = {}
for ir in irows:
    key = ir.get(itype_col) if itype_col else "ALL"
    layouts.setdefault(key, []).append(ir)
for k in layouts:
    layouts[k].sort(key=lambda x: (int(x[acc_col] or 0)))
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
    """이 행에 맞는 레이아웃 — 줄 바이트수와 총길이가 같은 것을 우선"""
    b = len(str(fr[text_col] or "").encode("cp949", errors="replace"))
    if type_col and fr.get(type_col) in layouts:
        return layouts[fr[type_col]]
    best = None
    for k, lay in layouts.items():
        tot = int(lay[-1][acc_col] or 0)
        if tot == b:
            return lay
        if best is None or abs(tot - b) < abs(int(best[-1][acc_col] or 0) - b):
            best = lay
    return best


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
bad, okc = [], 0
covered_fields = Counter()
for es, rows_ in sorted(people.items(), key=lambda x: EMP[x[0]][1]):
    emp_id, name = EMP[es]
    # 신고 레코드에서 금액 뽑기 (필드명과 함께)
    filed = {}
    for fr in rows_:
        for k, v in parse_line(fr[text_col], pick_layout(fr)):
            d = v.lstrip("0") or "0"
            if AMT.match(v.lstrip("-")) and int(d if d != "0" else 0) >= args.min:
                filed.setdefault(int(d), set()).add(k or "?")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            html, _t, _f, _m = W.render(cur, emp_id, args.yy)
    except Exception as e:
        bad.append((name, emp_id, [("렌더 실패", str(e)[:80])]))
        continue
    text = re.sub(r"<[^>]+>", " ", html)
    ours = {int(m.replace(",", ""))
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
