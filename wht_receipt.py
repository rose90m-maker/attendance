# -*- coding: utf-8 -*-
"""근로소득 원천징수영수증 생성 (초안 단계)

ERP `_TWPRAdjTotAbrIncomeHTML` 에 저장된 공식 서식 HTML 템플릿(귀속연도별)을
그대로 쓰고, `YLW#_DataN_*` 치환 토큰을 연말정산 확정 데이터로 채운다.

데이터 원천 (전부 ERP TAEIN DB, 읽기 전용):
  _TWPRAdjTotResult        대상자 마스터 — EmpID(사번)·이름·주소·우편번호·입퇴사
  _TWPRAdjTotEmpInfo       인적 플래그 (거주자/외국인/세대주 …)
  _TWPRAdjTotEmpInfoMapping  NtsItemSeq → 필드명 (토큰명과 대응)
  _TWPRAdjTotNtsMst        근무기간
  _TWPRAdjTotNtsIncomeSum  급여·상여·4대보험·기납부세액 합계
  _TWPRAdjTotIncomeTaxDeduc  소득공제 명세 (AdjItemSeq)
  _TWPRAdjTotItem          공제 항목명 마스터
  _TCACompany              징수의무자(회사)

  _TWPRAdjTotResultDtl     **ERP 의 계산 결과** — 근로소득공제·과세표준·산출세액·
                           결정세액·차감징수세액 등. AdjItemSeq 별 Amt(한도 적용 후)
  _TWPRAdjTotEmpDepenList  3쪽 부양가족별 명세 — 컬럼명이 서식 토큰명과 1:1

⚠️ 예전 이 자리에 "계산값은 ERP 가 출력 시점에 계산하며 DB에 저장돼 있지 않다"
   고 적혀 있었다. **틀린 말이었다** (2026-08-10 확인). 위 두 테이블에 다 있다.
   그 전제 때문에 2쪽 세액을 wht_calc.py 로 역공학해 다시 계산했고, 발급본
   하나(지창구 2025)에만 맞춰진 탓에 189명 중 186명에서 결정세액·산출세액이
   어긋났다. 3쪽도 같은 이유로 통째로 비어 있었다.

   지금은 ERP 계산 결과를 그대로 쓴다 (ERP_ITEM_NAME 매핑).
   wht_calc.py 는 ERP 조회가 실패할 때의 안전망으로만 남긴다.

   전 직원 검증:  python3 wht_watch.py --force
   매일 07:30 자동 감시가 등록돼 있고, 어긋날 때만 텔레그램으로 알린다.

사용:
    python wht_receipt.py --list 2024              # 대상자 목록
    python wht_receipt.py --emp 20030101 --yy 2024 # 사번으로 생성 → HTML
"""
import argparse
import os
import re
import sys
from datetime import date

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# 템플릿 토큰 — 대부분 'YLW#_' 지만 직인만 'Ylw#_' 로 섞여 있다
TOKEN_RE = re.compile(r"Y[Ll][Ww]#_[A-Za-z0-9_]+")

# 체크 표기는 CSS 클래스로 그린다 (.alterCheck.checked → 글자에 동그라미)
CHECK = "checked"


def _conn():
    import pymssql
    return pymssql.connect(
        server=os.environ["ERP_DB_HOST"],
        port=int(os.environ.get("ERP_DB_PORT", 14233)),
        user=os.environ["ERP_DB_USER"],
        password=os.environ["ERP_DB_PASSWORD"],
        database=os.environ.get("ERP_DB_NAME", "TAEIN"),
        charset="UTF-8",
    )


def _num(v):
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return ""
    if not n:
        return ""
    return f"{n:,.0f}"


def _date8(v, sep="-"):
    s = re.sub(r"\D", "", str(v or ""))
    if len(s) != 8 or s == "99991231":
        return ""
    return f"{s[:4]}{sep}{s[4:6]}{sep}{s[6:8]}"


# ── 데이터 로딩 ──────────────────────────────────────────────────
def load_template(cur, yy):
    cur.execute("""SELECT Head, Style, Page1, Page2, Page3, Detail, Footer
                   FROM _TWPRAdjTotAbrIncomeHTML WHERE YY=%s""", (yy,))
    r = cur.fetchone()
    if not r:
        raise ValueError(f"{yy} 귀속 서식 템플릿이 ERP에 없습니다")
    head, style, p1, p2, p3, det, foot = (x or "" for x in r)
    # 쪽 나누기 — 템플릿에는 없어서 붙인다 (.pagewrap 이 1쪽/2쪽/3쪽 컨테이너)
    # 쪽 나누기 — 두 번째 pagewrap 부터 '앞에서' 자른다.
    # page-break-after 를 쓰면 마지막 쪽 뒤에도 잘려 빈 페이지가 생긴다.
    # 쪽 나누기 — 두 번째 pagewrap 부터 '앞에서' 자른다.
    # page-break-after 를 쓰면 마지막 쪽 뒤에도 잘려 빈 페이지가 생긴다.
    # @page 에 size 를 넣으면 chromium 이 PDF scale 옵션을 무시하므로 margin 만 준다.
    style += """
<style>
  @page { margin: 6mm 5mm; }
  .pagewrap + .pagewrap { page-break-before: always; break-before: page; }
</style>"""
    return _fix_template(head + style + p1 + p2 + p3 + foot)  # Detail(부속명세)는 2차에서


# ERP 가 준 서식 원본의 오타. 별지 제24호서식에서 기본공제는 24.본인 / 25.배우자 /
# 26.부양가족 인데 ERP 템플릿에 <td colspan="6">26.본인</td> 로 적혀 있어 한 장에
# 26 번이 두 번 나온다 (2026-08-10 확인, 2025 귀속). 칸 배치와 금액은 정상이고
# 라벨 글자만 틀렸다. 법정서식이라 그대로 내보낼 수 없어 여기서 바로잡는다.
# ERP 가 원본을 고치면 이 치환은 저절로 아무 일도 하지 않는다.
TEMPLATE_FIX = [("<td colspan=\"6\">26.본인</td>", "<td colspan=\"6\">24.본인</td>")]


def _fix_template(tpl):
    for wrong, right in TEMPLATE_FIX:
        tpl = tpl.replace(wrong, right)
    return tpl

def find_target(cur, emp_no, yy):
    cur.execute("""SELECT EmpSeq, EmpName, EmpID, DeptName, PosName,
                          EntDate, RetDate, ResidZip, ResidAddr1, ResidAddr2, SMRetType
                   FROM _TWPRAdjTotResult WHERE YY=%s AND EmpID=%s""", (yy, emp_no))
    r = cur.fetchone()
    if not r:
        raise ValueError(f"{yy} 귀속 연말정산 대상에 사번 {emp_no} 이 없습니다")
    keys = ("emp_seq", "name", "emp_no", "dept", "pos", "ent", "ret",
            "zip", "addr1", "addr2", "ret_type")
    return dict(zip(keys, (str(v).strip() if isinstance(v, str) else v for v in r)))


def load_empinfo(cur, emp_seq, yy):
    """인적 플래그 — EmpInfoMapping 의 FieldName 을 키로"""
    cur.execute("""SELECT map.FieldName, e.AdjEmpInfo
                   FROM _TWPRAdjTotEmpInfo e
                   JOIN _TWPRAdjTotEmpInfoMapping map
                     ON map.NtsItemSeq = e.NtsItemSeq
                   WHERE e.YY=%s AND e.EmpSeq=%s""", (yy, emp_seq))
    return {f: (v or "").strip() for f, v in cur.fetchall()}


def load_income(cur, emp_seq, yy):
    """국세청 신고 **합계** — NtsItemName 을 키로

    ⚠️ SMPerCoAllType=3502001 이 '당사분'이라고 오래 적혀 있었지만 그렇지 않다.
    2025 귀속 데이터에 코드는 이것 하나뿐이고, 그 Amt 는 **종(전)근무지를 포함한
    합계**다 (김미선 급여 21,046,760 = 태인 20,219,760 + 삼미음향 827,000).
    주(현)근무지 값이 필요하면 Amt - PreAmt 로 구한다 → load_income_pre().
    """
    cur.execute("""SELECT i.NtsItemName, s.Amt
                   FROM _TWPRAdjTotNtsIncomeSum s
                   JOIN (SELECT DISTINCT YY, NtsItemSeq, NtsItemName
                         FROM _TWPRAdjTotNtsItem) i
                     ON i.YY=s.YY AND i.NtsItemSeq=s.NtsItemSeq
                   WHERE s.YY=%s AND s.EmpSeq=%s AND s.SMPerCoAllType=3502001""",
                (yy, emp_seq))
    out = {}
    for name, amt in cur.fetchall():
        out[(name or "").strip()] = out.get((name or "").strip(), 0) + float(amt or 0)
    return out


def load_income_pre(cur, emp_seq, yy):
    """종(전)근무지 합계 — load_income() 과 같은 테이블의 PreAmt 컬럼

    발급본(김미선 2025)과 대조해 확인한 관계 (2026-08-10):
        국민연금 1,153,570(Amt) - 100,570(PreAmt) = 1,053,000 = 서식의 현근무지
        고용보험   203,790       -   7,440        =   196,350  (= 827,000×0.9%)
    """
    cur.execute("""SELECT i.NtsItemName, s.PreAmt
                   FROM _TWPRAdjTotNtsIncomeSum s
                   JOIN (SELECT DISTINCT YY, NtsItemSeq, NtsItemName
                         FROM _TWPRAdjTotNtsItem) i
                     ON i.YY=s.YY AND i.NtsItemSeq=s.NtsItemSeq
                   WHERE s.YY=%s AND s.EmpSeq=%s AND s.SMPerCoAllType=3502001""",
                (yy, emp_seq))
    out = {}
    for name, amt in cur.fetchall():
        k = (name or "").strip()
        out[k] = out.get(k, 0) + float(amt or 0)
    return out


# Ⅱ 비과세소득 및 감면소득명세 — PrintMapping.SMType 이 둘을 가른다
NONTAX_SMTYPE = 3931003        # 비과세
REDUC_SMTYPE = 3931006         # 감면


def load_nontax(cur, emp_seq, yy):
    """Ⅱ 비과세소득 및 감면소득명세의 행.

    감면 항목(중소기업 취업자 T13 등)은 _TWPRAdjTotNtsIncomeSum 에,
    비과세 항목(야간근로수당 O01 등)은 **_TWPRAdjTotNtsNonTaxSum** 에 있다.
    NtsItemSeq 가 _TWPRAdjTotPrintMapping 에 있으면 이 영역에 인쇄되는
    항목이다. 서식에 찍는 코드(T13/O01)는 그 테이블의 Remark, 표시명
    ('(18)-1 야간근로수당')은 …Dtl.ForName 이다.

    예전에는 이 영역을 빈 행 14개로만 채워, 중소기업 취업자 감면 같은 항목이
    통째로 빠졌고 (이재현 2025 발급본 대조, 2026-08-10), IncomeSum 만 읽어
    비과세 행이 전부 빠졌다 (김동여 야간근로수당 1,658,760 — 국세청 신고파일
    대조로 발견, 2026-08-10).
    """
    cur.execute("""SELECT DISTINCT s.NtsItemSeq, m.SMType, m.DispSeq,
                          RTRIM(m.Remark), d.ForName, i.NtsItemName,
                          s.Amt, s.PreAmt
                   FROM (SELECT YY, EmpSeq, NtsItemSeq, Amt, PreAmt
                         FROM _TWPRAdjTotNtsIncomeSum
                         UNION ALL
                         SELECT YY, EmpSeq, NtsItemSeq, Amt, PreAmt
                         FROM _TWPRAdjTotNtsNonTaxSum) s
                   JOIN _TWPRAdjTotPrintMapping m
                     ON m.YY=s.YY AND m.Seq=s.NtsItemSeq
                   LEFT JOIN _TWPRAdjTotPrintMappingDtl d
                     ON d.YY=m.YY AND d.SMType=m.SMType AND d.Seq=m.Seq
                        AND d.LanguageSeq=1
                   LEFT JOIN (SELECT DISTINCT YY, NtsItemSeq, NtsItemName
                              FROM _TWPRAdjTotNtsItem) i
                     ON i.YY=s.YY AND i.NtsItemSeq=s.NtsItemSeq
                   WHERE s.YY=%s AND s.EmpSeq=%s
                     AND (s.Amt <> 0 OR s.PreAmt <> 0)
                     AND m.SMType IN (%s, %s)
                   ORDER BY m.DispSeq""",
                (yy, emp_seq, NONTAX_SMTYPE, REDUC_SMTYPE))
    out = []
    for seq, smtype, _disp, code, forname, itemname, amt, preamt in cur.fetchall():
        out.append({"seq": seq, "smtype": smtype,
                    "code": (code or "").strip(),
                    "title": (forname or itemname or "").strip(),
                    "name": (itemname or "").strip(),
                    "amt": float(amt or 0), "pre": float(preamt or 0)})
    return out


def build_nontax_rows(rows, works):
    """Ⅱ영역 반복행 — 열 구성은 Data2/Data3 와 같다"""
    out = []
    for r in rows:
        d = {"Data4_Title": r["title"], "Data4_NtsCd": r["code"],
             "Data4_Cur": _num(r["amt"] - r["pre"]),
             "Data4_TotAmt": _num(r["amt"])}
        for k in range(PRE_COLS):
            w = works[k] if k < len(works) else None
            d[f"Data4_Pre{k + 1}"] = _num(w["amt"].get(r["name"], 0)) if w else ""
        out.append(d)
    return out


# 20.비과세소득 계 / 20-1.감면소득 계 의 토큰. 이름만 보면 어느 쪽이 어느 행인지
# 알 수 없어(NonTax 가 감면 행일 수도 있다) 서식에서 직접 확인한다.
_SUM_TOKENS = {
    "Deduc": ("Data4_DeducSumCur", "Data4_DeducSumpre1", "Data4_DeducSumPre2",
              "Data4_DeducSumPre3", "Data4_DeducTotSumAmt"),
    "NonTax": ("Data4_NonTaxSumCur", "Data4_NonTaxSumPre1",
               "Data4_NonTaxSumPre2", "Data4_NonTaxSumPre3",
               "Data4_NonTaxSumAmt"),
}


def nontax_sum_tokens(tpl, rows, works):
    """20 / 20-1 계 행. 어느 토큰 묶음이 어느 행인지는 서식을 보고 정한다."""
    def totals(smtype):
        sel = [r for r in rows if r["smtype"] == smtype]
        tot = sum(r["amt"] for r in sel)
        cur_ = tot - sum(r["pre"] for r in sel)
        per = []
        for k in range(PRE_COLS):
            w = works[k] if k < len(works) else None
            per.append(_num(sum(w["amt"].get(r["name"], 0) for r in sel))
                       if w else "")
        return [_num(cur_)] + per + [_num(tot)]

    def is_reduc_row(tok):
        i = tpl.find("YLW#_" + tok)
        if i < 0:
            return None
        s, e = tpl.rfind("<tr", 0, i), tpl.find("</tr>", i)
        if s < 0 or e < 0:
            return None
        txt = re.sub(r"<[^>]+>", " ", re.sub(r"YLW#_\w+", " ", tpl[s:e]))
        if "20-1" in txt:
            return True          # 20-1.감면소득 계
        if re.search(r"(?<!\d)20\s*[.．]", txt):
            return False         # 20.비과세소득 계
        return None

    out = {}
    for group, toks in _SUM_TOKENS.items():
        kind = is_reduc_row(toks[0])
        if kind is None:
            continue             # 서식에서 못 찾으면 건드리지 않는다
        vals = totals(REDUC_SMTYPE if kind else NONTAX_SMTYPE)
        out.update(dict(zip(toks, vals)))
    return out


def _biz(no):
    """사업자등록번호 3018146359 → 301-81-46359"""
    s = re.sub(r"\D", "", str(no or ""))
    return f"{s[:3]}-{s[3:5]}-{s[5:]}" if len(s) == 10 else (no or "")


def load_prework(cur, emp_seq, yy):
    """종(전)근무지 — 회사정보(_TWPRAdjTotPreWork) + 근무지별 금액(…Dtl)

    서식 1쪽은 종(전)근무지를 3곳까지 세로 열로 찍는다. 예전에는 이 열을
    빈 문자열로 하드코딩해 두어 이직자 서식의 종(전) 열이 통째로 비고,
    주(현) 열에는 종전분이 섞인 합계가 들어갔다 (2026-08-10 발급본 대조로 발견).
    ERP DB 자동검사는 대조 대상이 전부 '합계' 항목이라 이 결함을 못 잡는다.
    """
    cur.execute("""SELECT Seq, PreCompanyName, PreTaxNo, WorkBegDate, WorkEndDate,
                          TaxReducBegDate, TaxReducEndDate
                   FROM _TWPRAdjTotPreWork
                   WHERE YY=%s AND EmpSeq=%s ORDER BY Seq""", (yy, emp_seq))
    works = [{"seq": r[0], "name": (r[1] or "").strip(),
              "biz_no": _biz(r[2]), "beg": r[3], "end": r[4],
              # ⑫감면기간 — 이재현 2025 발급본의 종(전) 열에 2025.01.01~2025.08.31
              # 이 찍혀 있는데 우리는 빈칸이었다 (2026-08-10 대조).
              "red_beg": r[5], "red_end": r[6], "amt": {}}
             for r in cur.fetchall()]
    if not works:
        return works

    cur.execute("""SELECT d.Seq, i.NtsItemName, d.PreAmt
                   FROM _TWPRAdjTotPreWorkDtl d
                   JOIN (SELECT DISTINCT YY, NtsItemSeq, NtsItemName
                         FROM _TWPRAdjTotNtsItem) i
                     ON i.YY=d.YY AND i.NtsItemSeq=d.NtsItemSeq
                   WHERE d.YY=%s AND d.EmpSeq=%s""", (yy, emp_seq))
    by_seq = {w["seq"]: w for w in works}
    for seq, name, amt in cur.fetchall():
        w = by_seq.get(seq)
        if w is None:
            continue
        k = (name or "").strip()
        w["amt"][k] = w["amt"].get(k, 0) + float(amt or 0)
    return works


def load_period(cur, emp_seq, yy):
    cur.execute("""SELECT BegDate, EndDate FROM _TWPRAdjTotNtsMst
                   WHERE YY=%s AND EmpSeq=%s""", (yy, emp_seq))
    r = cur.fetchone()
    return (r[0], r[1]) if r else ("", "")


def load_deducs(cur, emp_seq, yy):
    """소득공제 대상금액 — AdjItemName 패턴으로 분류해 wht_calc 입력을 만든다"""
    cur.execute("""SELECT i.AdjItemName, d.DeducAmt
                   FROM _TWPRAdjTotIncomeTaxDeduc d
                   LEFT JOIN _TWPRAdjTotItem i
                     ON i.YY=d.YY AND i.AdjItemSeq=d.AdjItemSeq
                   WHERE d.YY=%s AND d.EmpSeq=%s""", (yy, emp_seq))
    out = {"card_debit_cash": 0, "card_culture": 0, "med_full": 0, "med_etc": 0, "med_refund": 0,
           "edu_amount": 0, "donate_political": 0, "donate_special": 0,
           "donate_general": 0, "donate_religion": 0, "donate_hometown": 0,
           "pension_saving": 0, "pension_retire": 0, "monthly_rent": 0,
           "housing_saving": 0, "smb_reduc_pay": 0,
           "mort_fix_nonpay": 0, "mort_fix_or_nonpay": 0, "mort_etc": 0,
           "mort_y10_fix_or_nonpay": 0, "card_spend_cur": 0, "card_spend_prev": 0}
    unmapped = []
    for name, amt in cur.fetchall():
        nm = (name or "").strip()
        a = float(amt or 0)
        if not a and nm not in ("본인", "부양가족"):
            continue
        if nm.startswith("국민연금보험료"):
            out["np_pension"] = a
        elif nm.startswith("건강보험료"):
            out["health"] = a
        elif nm.startswith("고용보험료"):
            out["employ"] = a
        elif nm.startswith("주택임차차입금원리금상환액"):
            out["house_rent_principal"] = a
        elif nm.startswith("신용카드"):
            out["card_plastic"] = a
        elif nm.startswith(("직불카드", "현금영수증")):
            out["card_debit_cash"] += a
        elif nm.startswith(("문화체육", "도서공연")):
            # 연도별 명칭이 다르다 — 2024:'도서공연등사용분', 2025:'문화체육사용분'
            out["card_culture"] += a
        elif nm.startswith("전통시장"):
            out["card_tradition"] = a
        elif nm.startswith("대중교통"):
            out["card_transit"] = a
        elif nm.startswith("보장성보험료"):
            out["ins_guarantee"] = a
        elif nm.startswith(("본인의료비", "65세이상", "6세이하", "장애인의료비",
                            "난임", "장애인.건강보험산정특례자의료비",
                            "안경/렌즈구입비(본인)", "안경/렌즈구입비(65세이상)")):
            out["med_full"] += a
        elif nm.startswith(("그밖의공제대상자의료비", "안경/렌즈구입비(일반)")):
            out["med_etc"] += a
        elif nm.startswith("실손의료보험금"):
            out["med_refund"] += a
        elif "교육비" in nm or nm.startswith("중.고 교복"):
            out["edu_amount"] += a
        elif nm.startswith("장애인보장성보험료"):
            out["ins_disabled"] = out.get("ins_disabled", 0) + a
        elif nm.startswith("정치자금기부금"):
            out["donate_political"] += a
        elif nm.startswith("고향사랑기부금"):
            out["donate_hometown"] += a
        elif nm.startswith(("특례기부금", "특례(법정)기부금")):
            out["donate_special"] += a
        elif nm.startswith(("일반기부금(종교단체외)", "일반(=지정)기부금(종교단체외)")):
            out["donate_general"] += a
        elif nm.startswith(("일반기부금(종교단체)", "일반(=지정)기부금(종교단체)")):
            out["donate_religion"] += a
        elif nm.startswith("연금저축"):
            out["pension_saving"] += a
        elif nm.startswith("퇴직연금"):
            out["pension_retire"] += a
        elif nm.startswith("월세액"):
            out["monthly_rent"] += a
        elif nm.startswith("주택청약종합저축"):
            out["housing_saving"] += a
        elif nm.startswith("중소기업취업자에대한감면대상"):
            out["smb_reduc_pay"] += a
            if "(90%)" in nm:
                out["smb_reduc_rate"] = 0.90
            elif "(70%)" in nm:
                out["smb_reduc_rate"] = 0.70
        elif "저당차입금이자상환액" in nm:
            if "and비거치" in nm.replace(" ", "") or ("고정금리and" in nm):
                out["mort_fix_nonpay"] += a
            elif "10년" in nm:
                out["mort_y10_fix_or_nonpay"] += a
            elif "or비거치" in nm.replace(" ", "") or "고정금리or" in nm:
                out["mort_fix_or_nonpay"] += a
            else:
                out["mort_etc"] += a
        elif nm.endswith("전체 사용분"):
            # 소비증가분 특례용 — 귀속연도가 당해, 그 앞 연도가 전년
            (out.__setitem__("card_spend_cur", a) if nm.startswith(str(yy))
             else out.__setitem__("card_spend_prev", a))
        elif nm in ("본인", "부양가족", "경로우대(70세↑)"):
            pass                       # 인적공제는 DependPerCnt 로 별도 처리
        elif a:
            unmapped.append((nm, a))
    return out, unmapped


def load_persons(cur, emp_seq, yy):
    """인적공제 인원 — DependPerCnt"""
    cur.execute("""SELECT i.NtsItemName, COUNT(*)
                   FROM _TWPRAdjTotDependPerCnt p
                   JOIN (SELECT DISTINCT YY, NtsItemSeq, NtsItemName
                         FROM _TWPRAdjTotNtsItem) i
                     ON i.YY=p.YY AND i.NtsItemSeq=p.NtsItemSeq
                   WHERE p.YY=%s AND p.EmpSeq=%s
                   GROUP BY i.NtsItemName""", (yy, emp_seq))
    cnt = {n: c for n, c in cur.fetchall()}
    return {
        "has_spouse": bool(cnt.get("배우자", 0)),
        # 26.부양가족 = 본인·배우자를 뺀 기본공제 대상
        "persons_dependent": cnt.get("자녀외부양자", 0) + cnt.get("자녀부양자", 0),
        "persons_old": cnt.get("경로우대", 0),
        "persons_disabled": cnt.get("장애인", 0),
        "is_woman": bool(cnt.get("부녀자", 0)),
        "is_single_parent": bool(cnt.get("한부모", 0)),
        # 57.자녀세액공제 — 만 8세 이상 자녀
        "children_credit": cnt.get("만8세이상자녀", 0),
        "children_birth": cnt.get("출산입양자", 0),
    }


def load_family(cur, emp_seq, yy):
    """3쪽 부양가족 명세 — 관계·성명·공제 해당여부

    ERP 는 부양가족별 보험료·의료비·교육비를 개인 단위로 저장하지 않고
    합계만 갖고 있다(_TWPRAdjTotIncomeTaxDeduc). 그래서 금액 칸은 비우고
    관계·성명·공제표시만 채운다.
    """
    kinship = {3926001: "본인", 3926002: "배우자", 3926003: "직계존속",
               3926004: "배우자직계존속", 3926005: "형제자매",
               3926006: "직계비속(자녀)", 3926008: "직계비속(자녀외)",
               3926009: "위탁아동", 3926010: "수급자", 3926011: "기타"}
    cur.execute("""SELECT FamilySeq, FamilyName, SMKinShipSeq, SMDisableType
                   FROM _TWPRAdjTotDependFamily
                   WHERE YY=%s AND EmpSeq=%s ORDER BY FamilySeq""", (yy, emp_seq))
    fam = [{"seq": r[0], "name": (r[1] or "").strip(),
            "rel": kinship.get(r[2], ""), "disable": r[3] or 0,
            "flags": set()} for r in cur.fetchall()]

    cur.execute("""SELECT p.FamilySeq, i.NtsItemName
                   FROM _TWPRAdjTotDependPerCnt p
                   JOIN (SELECT DISTINCT YY, NtsItemSeq, NtsItemName
                         FROM _TWPRAdjTotNtsItem) i
                     ON i.YY=p.YY AND i.NtsItemSeq=p.NtsItemSeq
                   WHERE p.YY=%s AND p.EmpSeq=%s""", (yy, emp_seq))
    by_seq = {}
    for fseq, nm in cur.fetchall():
        by_seq.setdefault(fseq, set()).add((nm or "").strip())
    for f in fam:
        f["flags"] = by_seq.get(f["seq"], set())
    return fam


# 서식 항목 ↔ ERP 계산결과(_TWPRAdjTotResultDtl.AdjItemSeq)
#
# wht_calc.py 는 발급본 하나(지창구 2025)를 보고 역공학한 엔진이라, 안 겪어본
# 공제 조합에서 ERP 와 다른 값을 냈다 — 189명 중 186명에서 결정세액·산출세액·
# 근로소득세액공제 등이 어긋났다 (2026-08-10 확인).
#
# ERP 는 정답을 이미 갖고 있다. 다시 계산하지 말고 그 값을 쓴다.
# wht_calc 는 ERP 에 값이 없을 때의 대비책으로만 남긴다.


# 번호가 아니라 **항목명**으로 찾는다.
# AdjItemSeq 를 하드코딩하면, 기준으로 삼은 사람이 0원인 항목은 번호를 알 수
# 없어 매핑에서 빠진다. 실제로 지창구 2025 에는 부녀자·자녀세액공제·교육비·
# 중소기업감면이 없어 그 항목들이 통째로 누락됐다 (2026-08-10).
ERP_ITEM_NAME = {
    "총급여": 21,
    "근로소득공제": 22,
    "근로소득금액": 23,
    "본인": 24,
    "배우자": 25,
    "부양가족": 26,
    "경로우대(70세↑)": 27,
    "장애인": 28,
    "부녀자": 29,
    "한부모": 30,
    "국민연금보험료": 31,
    "차감소득금액": 36,
    "그밖의 소득공제계": 46,
    "종합소득과세표준": 48,
    "산출세액": 49,
    # 52.「조세특례제한법」제30조 — 중소기업 취업자 감면.
    # 이재현 2025 발급본에 473,817 이 찍히는데 매핑이 없어 wht_calc 계산값에
    # 기대고 있었다 (2026-08-10). 한도적용 후 값이 서식에 나가는 값이다.
    "중소기업취업자감면(A+B+C)한도적용": 52,
    "세액감면계": 54,                      # 54.세액감면 계 → Data6_Amt58
    "근로소득세액공제": 55,
    "공제대상자녀(세액공제)": 57,
    # 61~63 번 칸은 「공제대상금액」과 「세액공제액」이 따로 있다.
    # 세액공제액만 매핑했더니 대상금액 칸이 비었다 — 김미선 2025 의
    # 의료비 대상금액 939,500 이 그랬다 (2026-08-10).
    "보장성보험(세액공제)": 61,
    "보장성보험료(대상금액)": "61obj",
    # 61 행의 둘째 줄 — 장애인전용보장성 (유재영 2025: 78,976 / 대상 526,510).
    # 표기 변형을 모두 적는다. 없는 이름은 안 걸릴 뿐이다.
    "장애인전용보장성보험(세액공제)": "61dis",
    # 대상금액의 실제 ERP 항목명은 '장애인보장성보험료(대상금액)' 다 ('전용' 이
    # 빠진다 — 유재영 2025 실측). 변형도 함께 둔다.
    "장애인보장성보험료(대상금액)": "61dis_obj",
    "장애인전용보장성보험료(대상금액)": "61dis_obj",
    "장애인전용보장성보험(세액대상금액)": "61dis_obj",
    "의료비(세액공제)": 62,
    "의료비(대상금액)": "62obj",
    "교육비(세액공제)": 63,
    # 64.기부금 — ERP 는 Amt=한도적용 후 공제액을 갖는다. 김미선 2025 처럼
    # Amt=0, OrgAmt=1,500 인 사람은 발급본이 빈칸이다. 계산기는 무조건 15% 를
    # 찍어서 어긋났다 (10번째 결함, 2026-08-10 발급본 재확인으로 확정).
    # 대상금액 항목명은 계열마다 표기가 달라(세액대상금액/대상금액) 둘 다 적는다.
    # ERP 에 없는 이름은 그냥 안 걸릴 뿐이라 무해하다.
    "정치자금기부금(세액공제)_10만원이하": "64pol_lo",
    "정치자금기부금(대상금액)_10만원이하": "64pol_lo_obj",
    "정치자금기부금(세액대상금액)_10만원이하": "64pol_lo_obj",
    "정치자금기부금(세액공제)_10만원초과": "64pol_hi",
    "정치자금기부금(대상금액)_10만원초과": "64pol_hi_obj",
    "정치자금기부금(세액대상금액)_10만원초과": "64pol_hi_obj",
    "특례기부금(세액공제)": "64spec",
    "특례기부금(세액대상금액)": "64spec_obj",
    "특례기부금(대상금액)": "64spec_obj",
    "일반기부금(종교단체외-세액공제)": "64gen",
    "일반기부금(종교단체외-세액대상금액)": "64gen_obj",
    "일반기부금(종교단체외-대상금액)": "64gen_obj",
    "일반기부금(종교단체-세액공제)": "64rel",
    "일반기부금(종교단체-세액대상금액)": "64rel_obj",
    "일반기부금(종교단체-대상금액)": "64rel_obj",
    # 64㉯ 고향사랑기부금 — 토큰(Data6_Amt114~117)은 이미 배선돼 있는데 ERP
    # 매핑이 없어 계산기 값만 들어갔다 (김재구 2025: 90,909 가 어느 칸에도 없음).
    "고향사랑기부금(세액공제)_10만원이하": "64home_lo",
    "고향사랑기부금(대상금액)_10만원이하": "64home_lo_obj",
    "고향사랑기부금(세액대상금액)_10만원이하": "64home_lo_obj",
    "고향사랑기부금(세액공제)_10만원초과": "64home_hi",
    "고향사랑기부금(대상금액)_10만원초과": "64home_hi_obj",
    "고향사랑기부금(세액대상금액)_10만원초과": "64home_hi_obj",
    # 59.퇴직연금 / 60.연금저축 — 계산기 값이 ERP 한도적용 값과 어긋났다
    # (김영숙 178,369 · 배옥림 215,285, 국세청 신고파일 대조로 발견 2026-08-10).
    # 대상금액은 '(최종)' 이 한도적용 후 값이다.
    "퇴직연금(세액공제)": 59,
    "퇴직연금(대상금액)(최종)": "59obj",
    "연금저축(세액공제)": 60,
    "연금저축(대상금액)(최종)": "60obj",
    # 34㉯ 장기주택저당차입금 이자상환액 — 서식 9행이 통째로 미배선이었다
    # (구현우 5,214,623 등 7명, 국세청 신고파일 대조로 발견 2026-08-10).
    # ERP 항목명이 서식 행과 1:1 로 대응한다 (2011년=차입시기 2011년 이전).
    "2011년 저당차입금이자상환액(15년↓)": "34b_11_lt15",
    "2011년 저당차입금이자상환액(15~29년)": "34b_11_1529",
    "2011년 저당차입금이자상환액(30년↑)": "34b_11_ge30",
    "2011년 저당차입금이자상환액(고정금리and비거치상환)": "34b_11_fixnon",
    "2011년 저당차입금이자상환액(고정금리or비거치상환)": "34b_11_fixor",
    "2012년 저당차입금이자상환액(15년↑고정금리and비거치상환)": "34b_12_fixnon",
    "2012년 저당차입금이자상환액(15년↑고정금리or비거치상환)": "34b_12_fixor",
    "2012년 저당차입금이자상환액(15년↑기타대출)": "34b_12_etc",
    "2012년 저당차입금이자상환액(10년↑고정금리or비거치상환)": "34b_12_1015",
    # 42.신용카드등 소득공제 — ERP 의 '신용카드 등 사용금액'이 이름과 달리
    # **한도적용 후 공제금액**이다 (강태준 3,483,920 = 일반 3,000,000 + 추가
    # 483,920 · 박정민 890,354 · 김동욱 3,144,880 전부 신고파일 값과 일치).
    # 일반+추가를 합성하는 방식은 추가공제 항목명이 사람마다 달라 쓰지 않는다.
    "신용카드 등 사용금액": 41,
    # 70.월세액 — 계산기 값이 ERP 한도적용 값과 어긋났다
    # (유지윤 1,414,518 vs ERP 1,350,273, 신고파일 대조 2026-08-10)
    "월세액": 70,
    "월세액(대상금액)(집계)": "70obj",
    # 56.혼인세액공제 — 2025 신설. 서식 토큰 Data6_Amt118
    # (이복우 500,000, 국세청 신고파일 대조로 발견 2026-08-10)
    "혼인세액공제": "56marry",
    "표준세액공제": 66,
    "특별세액공제계": 65,
    "세액공제계": 71,
    "결정세액(소득세)": "73tax",
    "결정세액(지방소득세)": "73local",
    "기납부세액(소득세)": "75tax",
    "기납부세액(지방소득세)": "75local",
    "차감징수세액(소득세)": "77tax",
    "차감징수세액(지방소득세)": "77local",
}


def load_erp_result(cur, emp_seq, yy):
    """ERP 가 계산해 둔 결과를 **항목명 기준**으로 가져온다.

    {서식항목키: 금액}. Amt 는 한도를 적용한 뒤의 값이라 서식이 찍는 값과 같다.
    """
    cur.execute("""SELECT i.AdjItemName, d.Amt
                   FROM _TWPRAdjTotResultDtl d
                   LEFT JOIN _TWPRAdjTotItem i
                     ON i.YY=d.YY AND i.AdjItemSeq=d.AdjItemSeq
                   WHERE d.YY=%s AND d.EmpSeq=%s""", (yy, emp_seq))
    out = {}
    for nm, amt in cur.fetchall():
        key = ERP_ITEM_NAME.get((nm or "").strip())
        if key is None:
            continue
        try:
            out[key] = int(float(amt or 0))
        except (TypeError, ValueError):
            pass
    return out


def apply_erp_result(r, erp):
    """계산값을 ERP 값으로 덮어쓴다. ERP 에 없는 항목만 wht_calc 결과를 남긴다."""
    used = []
    for key, val in erp.items():
        if r.get(key) != val:
            used.append((key, r.get(key), val))
        r[key] = val
    return used


def load_depen_list(cur, emp_seq, yy):
    """3쪽 「78.소득·세액공제 명세」의 부양가족별 금액.

    ERP 는 이 표를 _TWPRAdjTotEmpDepenList 하나로 그린다 — 행=사람, 열=항목이라
    서식과 모양이 같고, **컬럼명이 서식 토큰명과 1:1 로 대응한다**
    (NtsPlastic → Data7_NtsPlastic). 그래서 매핑을 손으로 나열할 필요가 없다.

    예전에는 _TWPRAdjTotIncomeTaxDeduc(직원 단위 합계)만 보고 "ERP 는 개인별
    금액을 저장하지 않는다"고 판단해 금액 칸을 통째로 비웠다. 그 테이블에
    한정하면 맞는 말이었지만, 이 테이블을 못 찾았던 것이다 (2026-08-10 확인).
    """
    cur.execute(f"""SELECT * FROM _TWPRAdjTotEmpDepenList
                    WHERE YY=%s AND EmpSeq=%s ORDER BY FamilySeq""", (yy, emp_seq))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _amt(v):
    """금액 칸 — 0 이면 빈칸 (서식이 0 을 찍지 않는다)"""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return ""
    return f"{n:,}" if n else ""


def _mark(v):
    """인적공제 해당란 — ○ 표시"""
    return "○" if str(v or "").strip() in ("1", "Y", "○") else ""


# 값이 '해당 여부'인 컬럼 — 금액이 아니라 ○ 로 찍는다
FLAG_COLS = {"DependYn", "OldManDeducYn", "WomanDeducYn", "SingleFamYn",
             "DisabledYn", "ChildBirthYn", "Child6Yn", "MarryYn", "ChildCnt"}
# 금액도 표시값도 아닌 컬럼 — 서식에 안 나간다
SKIP_COLS = {"CompanySeq", "YY", "EmpSeq", "FamilySeq", "LastUserSeq",
             "LastDateTime", "DepenResidIdBNR", "EduDeducCd"}


REPEAT_BEGIN = "<!-- Data7_repeat Begin-->"
REPEAT_END = "Data7_repeat END-->"

# 3쪽에는 부양가족 반복 블록이 **두 개** 있고 마커 이름이 서로 다르다.
#   Data7_repeat  — 위쪽 인적공제·보험료·의료비·교육비 표
#   Data8_repeat  — 아래쪽 신용카드 등 사용액공제 표
# 그런데 **두 블록 안의 토큰은 모두 Data7_ 접두사**를 쓴다 (서식 원본 확인,
# 2026-08-10). 마커 이름만 보고 Data7 만 펼치면 신용카드 표의 개인별 행이
# 통째로 빈다. 두 마커 모두 같은 행 데이터로 펼쳐야 한다.
DEPEN_REPEAT_MARKERS = ("Data7", "Data8")


def _expand(tpl, marker, rows_values):
    """`<!-- {marker}_repeat Begin-->` ~ `{marker}_repeat END-->` 블록을
    rows_values(행별 토큰 dict 목록) 만큼 복제해 채운다."""
    b = tpl.find(f"<!-- {marker}_repeat Begin-->")
    e = tpl.find(f"{marker}_repeat END-->")
    if b < 0 or e < 0:
        return tpl
    e += len(f"{marker}_repeat END-->")
    row_tpl = tpl[b:e]
    out = "".join(
        TOKEN_RE.sub(lambda m: str(v.get(m.group(0)[5:], "")), row_tpl)
        for v in rows_values
    )
    return tpl[:b] + out + tpl[e:]


def _insert_section2_label(tpl, span):
    """Ⅱ 비과세소득·감면소득명세 영역 첫 행에 세로 레이블 셀을 끼운다.

    Ⅰ영역(rowspan=13 '근무처별소득명세')과 열 수를 맞추기 위한 것.
    없으면 Ⅱ영역 전체가 한 칸씩 왼쪽으로 밀린다.
    """
    marker = "Data3_Sum END-->"
    i = tpl.find(marker)
    if i < 0:
        return tpl
    i += len(marker)
    j = tpl.find("<tr", i)
    if j < 0:
        return tpl
    k = tpl.find(">", j)
    if k < 0:
        return tpl
    cell = (f'<td rowspan="{span}" class="labelVertical" '
            f'style="writing-mode:vertical-rl; text-align:center; '
            f'font-size:10px; letter-spacing:1px;">'
            f'Ⅱ비과세소득 및 감면소득명세</td>')
    return tpl[:k + 1] + cell + tpl[k + 1:]


def _period(beg, end):
    b, e = _date8(beg, "."), _date8(end, ".")
    return f"{b} ~ {e}" if b or e else ""


# 서식 1쪽이 세로로 찍는 소득 항목. (표시명, NtsItemName)
INCOME_ROWS = [
    ("⑬ 급여", "급여"),
    ("⑭ 상여", "상여"),
    ("⑮ 인정상여", "인정상여"),
    ("⑮-1 주식매수선택권 행사이익", "주식매수선택권행사이익"),
    ("⑮-2 우리사주조합인출금", "우리사주조합인출금"),
    ("⑮-3 임원 퇴직소득금액 한도초과액", "임원퇴직소득한도초과액"),
    ("⑮-4 직무발명보상금", "직무발명보상금"),
]
PRE_COLS = 3          # 서식이 받는 종(전)근무지 열 수


def build_income_rows(t, inc, pre, works, beg, end, co):
    """1쪽 '근무처별 소득명세' — 원본 서식의 행 구성 그대로

    Data2 = 근무처 정보(⑨~⑫), Data3 = 소득 항목(⑬~⑮-4)
    열 = 주(현) / 종(전)×3 / 합계

    inc 는 종(전)을 포함한 합계이므로 주(현) = inc - pre 로 갈라 넣는다.
    합계 열(Data3_TotAmt)은 inc 그대로다.
    """
    def _cols(fn):
        return {f"pre{k + 1}": (fn(works[k]) if k < len(works) else "")
                for k in range(PRE_COLS)}

    def row2(title, cur_val, fn):
        d = {"Data2_Title": title, "Data2_cur": cur_val}
        d.update({f"Data2_{k}": v for k, v in _cols(fn).items()})
        return d

    def row3(title, key):
        tot = inc.get(key, 0)
        d = {"Data3_Title": title,
             "Data3_Cur": _num(tot - pre.get(key, 0)),
             "Data3_TotAmt": _num(tot)}
        d.update({f"Data3_{k}": v for k, v in
                  _cols(lambda w: _num(w["amt"].get(key, 0))).items()})
        return d

    d2 = [
        row2("⑨ 근무처명", co["name"], lambda w: w["name"]),
        row2("⑩ 사업자등록번호", co["biz_no"], lambda w: w["biz_no"]),
        row2("⑪ 근무기간", _period(beg, end),
             lambda w: _period(w["beg"], w["end"])),
        row2("⑫ 감면기간", "",
             lambda w: _period(w["red_beg"], w["red_end"])),
    ]
    d3 = [row3(title, key) for title, key in INCOME_ROWS]

    # 16.계 — 주(현) / 종(전)별 / 전체
    keys = [k for _t, k in INCOME_ROWS]
    total = sum(inc.get(k, 0) for k in keys)
    sums = {"Data3_SumCur": _num(total - sum(pre.get(k, 0) for k in keys)),
            "Data3_TotAmt": _num(total)}
    sums.update({f"Data3_Sumpre{k + 1}":
                 (_num(sum(works[k]["amt"].get(x, 0) for x in keys))
                  if k < len(works) else "")
                 for k in range(PRE_COLS)})
    return d2, d3, sums


def expand_family_rows(tpl, depen, disable_map=None):
    """Page3 의 Data7_repeat 블록을 부양가족 수만큼 복제해 채운다.

    depen: load_depen_list() 결과. 컬럼명이 토큰명과 같으므로 그대로 옮긴다.
    disable_map: {FamilySeq: SMDisableType} — 장애인 코드만 다른 테이블에 있다.
    """
    disable_map = disable_map or {}

    def one(d, row_tpl):
        v = {}
        for col, val in d.items():
            if col in SKIP_COLS:
                continue
            key = f"Data7_{col}"
            if col in FLAG_COLS:
                v[key] = _mark(val)
            elif isinstance(val, (int, float)) or str(val or "").replace(
                    ".", "").replace("-", "").isdigit():
                v[key] = _amt(val)
            else:
                v[key] = str(val or "").strip()

        # 관계코드·내외국인은 숫자 그대로 찍는다 (금액 포맷이 아니다)
        v["Data7_DepenType"] = str(d.get("DepenType") or "").strip()
        v["Data7_DepenFrgnYn"] = str(d.get("DepenFrgnYn") or "").strip()
        v["Data7_DepenNm"] = str(d.get("DepenNm") or "").strip()

        # 주민번호는 DB 에 암호화(varbinary)로만 있어 복호화할 수 없다.
        # 본인 행은 ERP 도 번호 대신 '(근로자본인)' 을 찍으므로 그대로 맞춘다.
        v["Data7_DepenResidId"] = ("(근로자본인)"
                                   if str(d.get("DepenType") or "").strip() == "0"
                                   else "")

        # 장애인 코드는 _TWPRAdjTotDependFamily 에 있다
        dis = disable_map.get(d.get("FamilySeq"))
        v["Data7_DisableType"] = str(dis) if dis else ""

        # 서식에는 있으나 ERP 가 채우지 않는 칸
        v["Data7_IsMarry"] = ""
        v["Data7_IsEmptyPlace"] = ""

        # v 에 있는 토큰만 치환한다. 모르는 토큰을 ""로 지우면 누락이
        # missing 집계에 안 잡혀 문제가 드러나지 않는다 (예전 결함).
        return TOKEN_RE.sub(
            lambda m: str(v[m.group(0)[5:]]) if m.group(0)[5:] in v else m.group(0),
            row_tpl)

    out = tpl
    for marker in DEPEN_REPEAT_MARKERS:
        begin = f"<!-- {marker}_repeat Begin-->"
        end = f"{marker}_repeat END-->"
        pos = 0
        while True:
            b = out.find(begin, pos)
            if b < 0:
                break
            e = out.find(end, b)
            if e < 0:
                break
            e += len(end)
            row_tpl = out[b:e]
            rows = "".join(one(d, row_tpl) for d in depen) if depen else ""
            out = out[:b] + rows + out[e:]
            pos = b + len(rows)
    return out


def depen_summary_tokens(depen):
    """3쪽 '국세청 계 / 기타 계' 행과 인원수 칸.

    Data7_Sum<컬럼>  = 그 컬럼의 전 부양가족 합계
    Data7_SUM<컬럼>  = 그 플래그가 '1' 인 인원수
    """
    # 혼인세액공제 칸 — ERP 에 해당 컬럼이 없고 발급본에서도 비어 있다.
    # 명시적으로 빈 값을 넣어 두지 않으면 '미채움' 으로 보고돼 진짜 누락이 묻힌다.
    out = {"Data7_IsMarry": "", "Data7_MarryYn": "", "Data7_SUMMarryYn": ""}
    if not depen:
        return out
    cols = depen[0].keys()
    for col in cols:
        if col in SKIP_COLS:
            continue
        if col in FLAG_COLS:
            cnt = sum(1 for d in depen
                      if str(d.get(col) or "").strip() in ("1", "Y"))
            out[f"Data7_SUM{col}"] = str(cnt) if cnt else ""
        else:
            total = 0
            numeric = False
            for d in depen:
                try:
                    total += int(float(d.get(col) or 0))
                    numeric = True
                except (TypeError, ValueError):
                    numeric = False
                    break
            if numeric:
                out[f"Data7_Sum{col}"] = _amt(total)
    return out


def _seal_b64():
    """직인 PNG → base64 (증명서와 같은 파일을 쓴다). 없으면 빈 문자열"""
    import base64
    path = os.environ.get(
        "CERT_STAMP_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "static", "cert_stamp.png"))
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except OSError:
        return ""


def load_company(cur):
    cur.execute("""SELECT CompanyName, Owner, CompanyNo FROM _TCACompany
                   WHERE CompanySeq=1""")
    nm, owner, regno = cur.fetchone()
    # 사업자등록번호는 ERP 원천징수부 발급본(2026-08-06)에서 확인한 값
    return {"name": (nm or "").strip(), "owner": (owner or "").strip(),
            "law_no": (regno or "").strip(), "biz_no": "315-81-01759",
            "addr": "(28358) 충청북도 청주시 흥덕구 직지대로 243 (지동동)"}


# ── 토큰 채우기 ──────────────────────────────────────────────────
def build_values(cur, emp_no, yy, resid_id=""):
    t = find_target(cur, emp_no, yy)
    info = load_empinfo(cur, t["emp_seq"], yy)
    inc = load_income(cur, t["emp_seq"], yy)          # 종(전) 포함 합계
    pre = load_income_pre(cur, t["emp_seq"], yy)      # 그중 종(전)분
    works = load_prework(cur, t["emp_seq"], yy)       # 종(전)근무지 목록
    beg, end = load_period(cur, t["emp_seq"], yy)
    co = load_company(cur)
    # 하단 영수란(Data5)에서도 건강보험 '공제금액'이 필요하므로 v 를 만들기 전에 읽는다.
    deducs, unmapped = load_deducs(cur, t["emp_seq"], yy)
    persons = load_persons(cur, t["emp_seq"], yy)
    # 3쪽 부양가족 명세 — 합계행(Data7_Sum*/SUM*)은 반복블록 밖이라 v 에 넣는다.
    depen = load_depen_list(cur, t["emp_seq"], yy)

    def flag(field, val, n=None):
        """체크 표기 — 해당하면 CSS 클래스를 넣어 글자에 동그라미를 그린다"""
        return CHECK if (info.get(field, "") == str(val)) else ""

    pay = inc.get("급여", 0)
    bonus = inc.get("상여", 0)

    v = {
        # ── Data1: 관리·소득자·징수의무자 ──
        "Data1_EmpID": t["emp_no"],
        "Data1_EmpNM": t["name"],
        "Data1_ResidId": resid_id,                # 담당자가 처리 화면에서 입력
        "Data1_Addr": f"({t['zip']}) {t['addr2'] or t['addr1']}",
        "Data1_CONM": co["name"],
        "Data1_CoOwner": co["owner"],
        "Data1_BizNo": co["biz_no"],
        "Data1_LawRegNo": co["law_no"],
        "Data1_BizAddr": co["addr"],
        "Data1_SubBizSerl": "",
        "Data1_NationalityName": "대한민국" if info.get("NationalityCD", info.get("UMNationCD", "")) == "KR" else "",
        "Data1_NationalityCD": info.get("NationalityCD", info.get("UMNationCD", "")),
        "Data1_UMNationName": "대한민국" if info.get("UMNationCD", "") == "KR" else "",
        "Data1_UMNationCD": info.get("UMNationCD", ""),
        # 체크형 — ERP 값: ResidYn '1'=거주자, IsForeigner '0'=내국인, IsHouseHead …
        "Data1_ResidYn1": flag("ResidYn", 1, 1),
        "Data1_ResidYn2": flag("ResidYn", 0, 2),
        "Data1_IsForeigner1": flag("IsForeigner", 0, 1),
        "Data1_IsForeigner2": flag("IsForeigner", 1, 2),
        "Data1_IsFrgnTaxRate1": flag("IsFrgnTaxRate", 1, 1),
        "Data1_IsFrgnTaxRate2": flag("IsFrgnTaxRate", 0, 2),
        "Data1_IsFrgnComDispatcher1": "",
        "Data1_IsFrgnComDispatcher2": CHECK,
        "Data1_IsWkReligion1": "",
        "Data1_IsWkReligion2": CHECK,
        "Data1_IsHouseHead1": flag("IsHouseHead", 1, 1),
        "Data1_IsHouseHead2": flag("IsHouseHead", 0, 2),
        "Data1_IsHouseHead3": flag("IsHouseHead", 2, 3),
        "Data1_SMRetType1": CHECK if str(t["ret_type"]).endswith("1") else "",
        "Data1_SMRetType2": CHECK if str(t["ret_type"]).endswith("2") else "",
        "Data1_IsBizTax1": "",
        "Data1_IsBizTax2": CHECK,
        # Data2/Data3 는 반복행이라 render() 에서 펼친다. 16.계 는 build_income_rows
        # 가 만들어 v 에 합쳐 넣는다 (아래 v.update(sums)).
        # 20 / 20-1 계는 render() 에서 서식을 보고 채운다 (nontax_sum_tokens).
        # ── Data5: 기납부세액·보험료 ──
        # 75.주(현)근무지(Data5_*_TC)는 ERP 값이 필요해서 아래 v.update 에서 넣는다.
        # 74 는 그 아래에서 근무지별로 채운다.
        "Data5_SpecialTaxTax_TC": "",
        # 하단 영수란은 두 칸이다. 템플릿 순서가 Tot → Cur 이고, 발급본은
        # 「국민연금(현근무지) 1,153,570 ( 1,053,000 )」 처럼 합계를 먼저 찍는다.
        # 예전에는 두 칸에 같은 값을 넣어, 이직자도 현근무지분이 합계로 보였다.
        "Data5_NPCurAmt": _num(inc.get("국민연금보험", 0)
                               - pre.get("국민연금보험", 0)),
        "Data5_NPTotAmt": _num(inc.get("국민연금보험", 0)),
        # 건강보험은 급여대장 합계(국민건강보험+정산분)가 아니라 33.㉮ 의 '공제금액'을 쓴다.
        # ERP 발급본이 하단 영수란에도 공제금액을 찍기 때문이다.
        # 예전에는 정산분까지 더해 2,367,230 이 나왔고 ERP 는 2,347,130 이라,
        # 같은 장 안에서 건강보험료가 두 값으로 보였다 (2026-08-10 지창구 2025 대조).
        "Data5_MedCurAmt": _num(deducs.get("health", 0)
                                - pre.get("국민건강보험", 0)),
        "Data5_MedTotAmt": _num(deducs.get("health", 0)),
        "Data5_HireCurAmt": _num(inc.get("고용보험", 0) - pre.get("고용보험", 0)),
        "Data5_HireTotAmt": _num(inc.get("고용보험", 0)),
        "Data5_PrintDate": date.today().strftime("%Y년   %m월   %d일"),
        "Data5_TaxName": co["name"],
        "Data5_Owner": co["owner"],
        "Data5_TaxOffice": "청주",
        "Data5_SealPhoto": _seal_b64(),          # 징수의무자 직인
    }

    # 74.종(전)근무지 — 사업자등록번호와 그 근무지의 결정세액
    for k in range(PRE_COLS):
        w = works[k] if k < len(works) else None
        v[f"Data5_P{k + 1}BizNo"] = w["biz_no"] if w else ""
        v[f"Data5_Tax_Pre{k + 1}"] = _num(w["amt"].get("소득세", 0)) if w else ""
        v[f"Data5_ResidTax_Pre{k + 1}"] = (
            _num(w["amt"].get("지방소득세", 0)) if w else "")
        v[f"Data5_SpecialTaxTax_Pre{k + 1}"] = ""

    # ── 정산명세 (2쪽) — wht_calc 로 법정 산식 계산 ──
    from wht_calc import compute
    calc_in = dict(gross=pay + bonus,
                   prepaid_tax=inc.get("소득세", 0),
                   prepaid_local=inc.get("지방소득세", 0),
                   **persons, **deducs)
    r = compute(calc_in)
    # ERP 가 이미 계산해 둔 값으로 덮어쓴다 (있는 항목만).
    # 이렇게 하면 공제 조합이 어떻든 ERP 발급본과 구조적으로 일치한다.
    _erp = load_erp_result(cur, t["emp_seq"], yy)
    _diff = apply_erp_result(r, _erp)
    # 72.결정세액(49-54-71)은 1쪽 73.결정세액(소득세)과 같은 값이다. 72 는 ERP
    # 항목이 따로 없어 계산기 값이 남는데, 49·54·71 이 ERP 값으로 덮인 뒤에는
    # 계산기 72 와 어긋날 수 있다 (강미예 2025: 계산기 242,917 vs ERP 229,417 =
    # 49-54-71). 서식에 산식이 인쇄돼 있어 받는 쪽이 검산하면 바로 보인다.
    if "73tax" in _erp:
        r[72] = _erp["73tax"]
    # 61~64 특별세액공제 칸은 ERP 만 믿는다. ERP 가 항목을 안 준 사람은 공제가
    # 적용되지 않은 것이므로 빈칸이어야 한다. 계산기 값을 남겨두면 발급본은
    # 빈칸인데 우리만 값을 찍는다 — 김미선(기부금 1,500), 김동여·하홍경
    # (표준세액공제 선택자인데 61·63 에 계산기 값 잔존, 2026-08-10).
    for _k in (61, "61obj", "61dis", "61dis_obj", 62, "62obj", 63, "63obj",
               "64pol_lo", "64pol_lo_obj", "64pol_hi", "64pol_hi_obj",
               "64home_lo", "64home_lo_obj", "64home_hi", "64home_hi_obj",
               "64spec", "64spec_obj", "64gen", "64gen_obj",
               "64rel", "64rel_obj",
               # 59·60 연금계좌·70 월세도 같은 규칙 — ERP 에 없으면 미적용 = 빈칸
               59, "59obj", 60, "60obj", 70, "70obj"):
        if _k not in _erp:
            r[_k] = 0
    if _diff:
        print(f"  ℹ️  ERP 값으로 보정 {len(_diff)}건 "
              f"(계산기와 달랐던 항목): "
              + ", ".join(str(k) for k, _o, _n in _diff[:8]))

    def _tc(erp_key, item):
        """75.주(현)근무지 = ERP 기납부세액 - 종(전)근무지 결정세액.

        ERP 에 그 항목이 없으면 급여집계에서 종(전)분을 뺀 값으로 물러난다.
        """
        pre_sum = sum(w["amt"].get(item, 0) for w in works)
        if erp_key in r:
            return r[erp_key] - pre_sum
        return inc.get(item, 0) - pre.get(item, 0)

    # Data6 토큰 ↔ 서식 항목 (템플릿 위치 분석으로 확정, 지창구 2025 대조 검증)
    v.update({
        "Data6_Amt1": _num(r[21]), "Data6_Amt2": _num(r[22]),
        "Data6_Amt3": _num(r[23]), "Data6_Amt4": _num(r[24]),
        "Data6_Amt5": _num(r[25]),
        "Data6_Familys": str(r["26cnt"]) if r["26cnt"] else "",
        "Data6_Amt6": _num(r[26]),
        "Data6_old70Person": str(r["27cnt"]) if r["27cnt"] else "",
        "Data6_Amt7": _num(r[27]),
        "Data6_Disabled": str(r["28cnt"]) if r["28cnt"] else "",
        "Data6_Amt8": _num(r[28]), "Data6_Amt9": _num(r[29]),
        "Data6_Amt10": _num(r[30]),
        "Data6_Amt11": _num(r[31]), "Data6_Amt103": _num(r[31]),
        "Data6_Amt16": _num(deducs.get("health", 0)),
        "Data6_Amt108": _num(deducs.get("health", 0)),
        "Data6_Amt17": _num(deducs.get("employ", 0)),
        "Data6_Amt109": _num(deducs.get("employ", 0)),
        "Data6_Amt18": _num(r["34rent"]),
        # 34㉯ 장기주택저당차입금 9행 — ERP 값만 쓴다 (없으면 빈칸)
        "Data6_Amt20": _num(r.get("34b_11_lt15", 0)),
        "Data6_Amt21": _num(r.get("34b_11_1529", 0)),
        "Data6_Amt22": _num(r.get("34b_11_ge30", 0)),
        "Data6_Amt23": _num(r.get("34b_11_fixnon", 0)),
        "Data6_Amt24": _num(r.get("34b_11_fixor", 0)),
        "Data6_Amt42": _num(r.get("34b_12_fixnon", 0)),
        "Data6_Amt43": _num(r.get("34b_12_fixor", 0)),
        "Data6_Amt44": _num(r.get("34b_12_etc", 0)),
        "Data6_Amt45": _num(r.get("34b_12_1015", 0)),
        "Data6_Amt26": _num(r[35]), "Data6_Amt27": _num(r[36]),
        "Data6_Amt34": _num(r[41]), "Data6_Amt40": _num(r[46]),
        "Data6_Amt41": _num(r[47]),
        "Data6_Amt52": _num(r[48]), "Data6_Amt53": _num(r[49]),
        # 57.자녀세액공제 — 인원/금액, 출산·입양
        "Data6_DeducChild": str(r["57cnt"]) if r["57cnt"] else "",
        "Data6_Amt60": _num(r[57]),
        "Data6_Amt118": _num(r.get("56marry", 0)),   # 56.혼인세액공제 (ERP 만)
        "Data6_ChildBirthCnt": str(r["57birth_cnt"]) if r["57birth_cnt"] else "",
        "Data6_Amt62": "",
        "Data6_Amt56": _num(r[52]),                     # 52.조특법 §30 (중소기업감면)
        "Data6_Amt58": _num(r[54]), "Data6_Amt59": _num(r[55]),
        "Data6_Amt31": _num(r["39sub"]),                # 39㉯ 주택청약종합저축
        "Data6_Amt65": _num(r["59obj"]), "Data6_Amt66": _num(r[59]),
        "Data6_Amt67": _num(r["60obj"]), "Data6_Amt68": _num(r[60]),
        "Data6_Amt92": _num(r["70obj"]), "Data6_Amt93": _num(r[70]),
        "Data6_Amt114": _num(r["64home_lo_obj"]), "Data6_Amt115": _num(r["64home_lo"]),
        "Data6_Amt116": _num(r["64home_hi_obj"]), "Data6_Amt117": _num(r["64home_hi"]),
        "Data6_Amt69": _num(r["61obj"]), "Data6_Amt70": _num(r[61]),
        # 61 행 둘째 줄 — 장애인전용보장성 (유재영 2025: 대상 526,510 / 공제 78,976.
        # 이 두 토큰이 미배선이라 ERP 값이 갈 곳이 없었다 — 11번째 결함)
        "Data6_Amt71": _num(r.get("61dis_obj", 0)),
        "Data6_Amt72": _num(r.get("61dis", 0)),
        "Data6_Amt73": _num(r["62obj"]), "Data6_Amt74": _num(r[62]),
        "Data6_Amt75": _num(r["63obj"]), "Data6_Amt76": _num(r[63]),
        "Data6_Amt77": _num(r["64pol_lo_obj"]), "Data6_Amt78": _num(r["64pol_lo"]),
        "Data6_Amt79": _num(r["64pol_hi_obj"]), "Data6_Amt80": _num(r["64pol_hi"]),
        "Data6_Amt96": _num(r["64spec_obj"]), "Data6_Amt82": _num(r["64spec"]),
        "Data6_Amt99": _num(r["64gen_obj"]), "Data6_Amt100": _num(r["64gen"]),
        "Data6_Amt101": _num(r["64rel_obj"]), "Data6_Amt102": _num(r["64rel"]),
        "Data6_Amt87": _num(r[65]), "Data6_Amt88": _num(r[66]),
        "Data6_Amt94": _num(r[71]), "Data6_Amt95": _num(r[72]),
        "Data6_Amt110": str(r[82]) if r[82] else "",
        # 1쪽 세액명세 최종값
        "Data5_Tax_Final": _num(r["73tax"]),
        "Data5_ResidTax_Final": _num(r["73local"]),
        "Data5_SpecialTax_Final": "",
        # 75.주(현)근무지 기납부세액 — 위에서 급여집계로 넣은 값을 ERP 값으로 덮는다.
        # 73 과 77 은 ERP 값인데 75 만 급여집계에서 오면 73-74-75-76 이 77 과 맞지
        # 않는다. 박상현 2025 에서 소득세 4원·지방소득세 9원이 어긋났다
        # (2026-08-10, 발급본 대조 중 검산으로 발견).
        # ERP 의 '기납부세액'은 74+75 합계이므로 종(전) 결정세액을 뺀다.
        "Data5_Tax_TC": _num(_tc("75tax", "소득세")),
        "Data5_ResidTax_TC": _num(_tc("75local", "지방소득세")),
        "Data5_Tax_Deducted": _num(r["77tax"]) or "0",
        "Data5_ResidTax_Deducted": _num(r["77local"]) or "0",
        "Data5_SpecialTax_Deducted": "",
        "Data5_Tax_Spec": "", "Data5_ResidTax_Spec": "", "Data5_SpecialTax_Spec": "",
    })
    # 16.계 — 주(현)/종(전)별/전체. 반복블록 밖이라 여기서 넣어야 한다.
    # Data3_TotAmt 는 반복블록 안에도 같은 이름으로 있는데, _expand() 가 블록 안
    # 토큰을 먼저 소비하므로 여기 값은 합계행에만 들어간다. 예전에는 이 키가
    # 아예 없어 **16.계 총계 칸이 빈칸으로 나갔다** (2026-08-10 발견).
    _d2, _d3, sums = build_income_rows(t, inc, pre, works, beg, end, co)
    v.update(sums)
    # 3쪽 '국세청 계 / 기타 계' 행 + 인적공제 인원수
    v.update(depen_summary_tokens(depen))

    if unmapped:
        print("⚠️  미분류 공제 항목 (검토 필요):")
        for nm, a in unmapped:
            print(f"    {nm}: {a:,.0f}")
    return t, v, depen


def render(cur, emp_no, yy, resid_id=""):
    tpl = load_template(cur, yy)
    t, values, depen = build_values(cur, emp_no, yy, resid_id)
    # 반복행 펼치기 — 1쪽 근무처/소득명세, 3쪽 부양가족
    inc = load_income(cur, t["emp_seq"], yy)
    pre = load_income_pre(cur, t["emp_seq"], yy)
    works = load_prework(cur, t["emp_seq"], yy)
    beg, end = load_period(cur, t["emp_seq"], yy)
    d2, d3, _ = build_income_rows(t, inc, pre, works, beg, end, load_company(cur))
    tpl = _expand(tpl, "Data2", d2)
    tpl = _expand(tpl, "Data3", d3)
    # 비과세·감면 명세(Ⅱ) — 실제 항목을 먼저 놓고 나머지는 원본처럼 빈 행.
    # ⚠️ Ⅰ영역은 왼쪽에 세로 레이블 셀(rowspan=13)이 있어 행마다 28열인데
    #    Data4_repeat 에는 그 셀이 없어 27열이다. 그대로 두면 표가 한 칸씩 밀린다.
    #    → 첫 행에 레이블 셀을 끼워 넣는다 (전체 행 + 20 + 20-1 을 덮는다).
    nontax = load_nontax(cur, t["emp_seq"], yy)
    d4 = build_nontax_rows(nontax, works)
    ROWS = max(14, len(d4))                    # 원본 서식의 행 수를 유지한다
    tpl = _expand(tpl, "Data4", d4 + [{}] * (ROWS - len(d4)))
    tpl = _insert_section2_label(tpl, ROWS + 2)
    values.update(nontax_sum_tokens(tpl, nontax, works))
    # 장애인 코드만 _TWPRAdjTotDependFamily 에 있어 FamilySeq 로 붙인다
    disable_map = {f["seq"]: f["disable"]
                   for f in load_family(cur, t["emp_seq"], yy) if f["disable"]}
    tpl = expand_family_rows(tpl, depen, disable_map)

    filled = set()
    missing = set()

    def sub(mt):
        key = mt.group(0)[5:]          # 'YLW#_' 제거
        if key in values:
            filled.add(key)
            return str(values[key])
        missing.add(key)
        return ""

    html = TOKEN_RE.sub(sub, tpl)
    return html, t, sorted(filled), sorted(missing)


# ── 앱(공문서관리) 연동 ──────────────────────────────────────────
DOC_TYPE = "원천징수영수증"


def available_years(emp_no):
    """이 직원의 연말정산 확정 귀속연도 (최신순)"""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""SELECT YY FROM _TWPRAdjTotResult
                       WHERE EmpID=%s AND IsAdjTotConfirm='1'
                       ORDER BY YY DESC""", (emp_no,))
        return [str(r[0]).strip() for r in cur.fetchall()]
    finally:
        conn.close()


def html_to_pdf(html):
    """HTML → PDF (headless Chromium)

    서식이 복잡한 표라서 브라우저 렌더링이 가장 정확하다.
    playwright 가 없거나 브라우저 미설치면 RuntimeError.
    """
    import tempfile
    from playwright.sync_api import sync_playwright

    with tempfile.NamedTemporaryFile("w", suffix=".html", encoding="utf-8",
                                     delete=False) as f:
        f.write(html)
        src = f.name
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox"])
            pg = b.new_page()
            pg.goto(f"file://{src}")
            pg.emulate_media(media="print")
            # scale 0.9 — 1.0 이면 1쪽 내용이 A4 를 살짝 넘겨 4쪽이 된다
            data = pg.pdf(format="A4", print_background=True, scale=0.9,
                          margin={"top": "6mm", "bottom": "6mm",
                                  "left": "5mm", "right": "5mm"})
            b.close()
        return data
    finally:
        os.unlink(src)


def generate(emp_no, yy, as_pdf=True, resid_id="", task=""):
    # task 는 재직/경력증명서 전용이라 여기선 쓰지 않는다 (호출부 인터페이스 통일용)
    """원천징수영수증 생성 → (bytes, 파일명)

    ERP 는 읽기만 한다. 주민등록번호는 암호화라 빈칸으로 나가고
    담당자가 채운다 (2026-08-06 합의).
    PDF 변환이 불가한 환경이면 HTML 로 돌려준다.
    """
    conn = _conn()
    cur = conn.cursor()
    try:
        html, t, filled, missing = render(cur, emp_no, yy, resid_id)
    finally:
        conn.close()
    name = re.sub(r"[^\w가-힣]", "", t["name"] or "")
    base = f"{DOC_TYPE}_{name}_{yy}귀속"
    if as_pdf:
        try:
            return html_to_pdf(html), f"{base}.pdf"
        except Exception as e:
            # PDF 변환 실패는 발급 자체를 막지 않는다 — HTML 로 내보낸다
            print(f"  ⚠️  PDF 변환 실패 → HTML 로 대체: {type(e).__name__}: {str(e)[:100]}")
    return html.encode("utf-8"), f"{base}.html"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", metavar="YY", help="귀속연도 대상자 목록")
    ap.add_argument("--emp", help="사번")
    ap.add_argument("--yy", help="귀속연도 (예: 2024)")
    ap.add_argument("--out", help="저장 경로 (기본: 원천징수영수증_<이름>_<YY>귀속.pdf)")
    ap.add_argument("--html", action="store_true", help="PDF 대신 HTML 로 저장")
    args = ap.parse_args()

    conn = _conn()
    cur = conn.cursor()
    try:
        if args.list:
            cur.execute("""SELECT EmpID, EmpName, DeptName, EntDate, RetDate
                           FROM _TWPRAdjTotResult WHERE YY=%s ORDER BY EmpName""",
                        (args.list,))
            for r in cur.fetchall():
                print(f"  {r[0]}  {r[1]:8s} {str(r[2] or ''):10s} "
                      f"{_date8(r[3])} ~ {_date8(r[4])}")
            return 0

        if not (args.emp and args.yy):
            ap.error("--emp 사번 --yy 귀속연도 를 주거나 --list YY 를 쓰세요")

        html, t, filled, missing = render(cur, args.emp, args.yy)
        print(f"토큰 채움 {len(filled)}개 / 미채움 {len(missing)}개")

        if args.html:
            out = args.out or f"원천징수영수증_{t['name']}_{args.yy}.html"
            open(out, "w", encoding="utf-8").write(html)
            print(f"생성: {out}  ({len(html):,}자)")
        else:
            # 앱과 같은 경로로 만든다 (PDF, 변환 불가하면 HTML 폴백)
            data, name = generate(args.emp, args.yy)
            out = args.out or name
            with open(out, "wb") as f:
                f.write(data)
            pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
            kind = "PDF" if data[:5] == b"%PDF-" else "HTML"
            print(f"생성: {out}  ({len(data):,} bytes"
                  + (f", {pages}쪽" if kind == "PDF" else "") + f", {kind})")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
