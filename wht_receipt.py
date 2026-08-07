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

⚠️ 미완: 2쪽 정산명세의 계산값(근로소득공제·과세표준·산출세액·결정세액 등)은
   ERP 가 수식 엔진으로 출력 시점에 계산하며 DB에 저장돼 있지 않다.
   해당 토큰은 빈칸으로 나온다. → ERP 실발급본과 대조하며 채워 나간다 (역공학).
   fill_coverage() 로 채움/미채움 현황을 볼 수 있다.

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
    return head + style + p1 + p2 + p3 + foot   # Detail(부속명세)는 2차에서

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
    """국세청 신고 합계 — NtsItemName 을 키로 (당사분 3502001)"""
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


REPEAT_BEGIN = "<!-- Data7_repeat Begin-->"
REPEAT_END = "Data7_repeat END-->"


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


def build_income_rows(t, inc, beg, end, co):
    """1쪽 '근무처별 소득명세' — 원본 서식의 행 구성 그대로

    Data2 = 근무처 정보(⑨~⑫), Data3 = 소득 항목(⑬~⑮-4)
    열 = 주(현) / 종(전)×3 / 합계
    """
    def row2(title, cur_val):
        return {"Data2_Title": title, "Data2_cur": cur_val,
                "Data2_pre1": "", "Data2_pre2": "", "Data2_pre3": ""}

    def row3(title, amt):
        v = _num(amt)
        return {"Data3_Title": title, "Data3_Cur": v,
                "Data3_pre1": "", "Data3_pre2": "", "Data3_pre3": "",
                "Data3_TotAmt": v}

    pay, bonus = inc.get("급여", 0), inc.get("상여", 0)
    d2 = [
        row2("⑨ 근무처명", co["name"]),
        row2("⑩ 사업자등록번호", co["biz_no"]),
        row2("⑪ 근무기간", f"{_date8(beg, '.')} ~ {_date8(end, '.')}"),
        row2("⑫ 감면기간", ""),
    ]
    d3 = [
        row3("⑬ 급여", pay),
        row3("⑭ 상여", bonus),
        row3("⑮ 인정상여", 0),
        row3("⑮-1 주식매수선택권 행사이익", 0),
        row3("⑮-2 우리사주조합인출금", 0),
        row3("⑮-3 임원 퇴직소득금액 한도초과액", 0),
        row3("⑮-4 직무발명보상금", 0),
    ]
    return d2, d3, pay + bonus


def expand_family_rows(tpl, fam):
    """Page3 의 Data7_repeat 블록을 부양가족 수만큼 복제해 채운다"""
    b = tpl.find(REPEAT_BEGIN)
    e = tpl.find(REPEAT_END)
    if b < 0 or e < 0:
        return tpl
    e += len(REPEAT_END)
    row_tpl = tpl[b:e]

    def one(f):
        v = {
            "Data7_DepenType": f["rel"],
            "Data7_DepenNm": f["name"],
            "Data7_DepenResidId": "",          # 주민번호는 암호화 — 담당자 기입
            "Data7_DepenFrgnYn": "",
            "Data7_DependYn": "O" if f["flags"] else "",
            "Data7_OldManDeducYn": "O" if "경로우대" in f["flags"] else "",
            "Data7_WomanDeducYn": "O" if "부녀자" in f["flags"] else "",
            "Data7_SingleFamYn": "O" if "한부모" in f["flags"] else "",
            "Data7_DisableType": str(f["disable"]) if f["disable"] else "",
            "Data7_ChildCnt": "O" if "만8세이상자녀" in f["flags"] else "",
            "Data7_MarryYn": "",
            "Data7_ChildBirthYn": "O" if "출산입양자" in f["flags"] else "",
            "Data7_IsEmptyPlace": "",
        }
        return TOKEN_RE.sub(lambda m: str(v.get(m.group(0)[5:], "")), row_tpl)

    rows = "".join(one(f) for f in fam) if fam else ""
    return tpl[:b] + rows + tpl[e:]


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
def build_values(cur, emp_no, yy):
    t = find_target(cur, emp_no, yy)
    info = load_empinfo(cur, t["emp_seq"], yy)
    inc = load_income(cur, t["emp_seq"], yy)
    beg, end = load_period(cur, t["emp_seq"], yy)
    co = load_company(cur)

    def flag(field, val, n=None):
        """체크 표기 — 해당하면 CSS 클래스를 넣어 글자에 동그라미를 그린다"""
        return CHECK if (info.get(field, "") == str(val)) else ""

    pay = inc.get("급여", 0)
    bonus = inc.get("상여", 0)

    v = {
        # ── Data1: 관리·소득자·징수의무자 ──
        "Data1_EmpID": t["emp_no"],
        "Data1_EmpNM": t["name"],
        "Data1_ResidId": "",                      # 담당자 수기 기입 (2026-08-06 합의)
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
        # Data2/Data3 는 반복행이라 render() 에서 펼친다. 여기서는 합계만.
        "Data3_SumCur": _num(pay + bonus),        # 16.계
        "Data3_Sumpre1": "", "Data3_Sumpre2": "", "Data3_Sumpre3": "",
        "Data4_DeducSumCur": "", "Data4_NonTaxSumCur": "",   # 20/20-1 계
        # ── Data5: 기납부세액·보험료 ──
        "Data5_Tax_TC": _num(inc.get("소득세", 0)),
        "Data5_ResidTax_TC": _num(inc.get("지방소득세", 0)),
        "Data5_SpecialTaxTax_TC": "",
        "Data5_NPCurAmt": _num(inc.get("국민연금보험", 0)),
        "Data5_NPTotAmt": _num(inc.get("국민연금보험", 0)),
        "Data5_MedCurAmt": _num(inc.get("국민건강보험", 0) + inc.get("국민건강보험-정산분", 0)),
        "Data5_MedTotAmt": _num(inc.get("국민건강보험", 0) + inc.get("국민건강보험-정산분", 0)),
        "Data5_HireCurAmt": _num(inc.get("고용보험", 0)),
        "Data5_HireTotAmt": _num(inc.get("고용보험", 0)),
        "Data5_PrintDate": date.today().strftime("%Y년   %m월   %d일"),
        "Data5_TaxName": co["name"],
        "Data5_Owner": co["owner"],
        "Data5_TaxOffice": "청주",
        "Data5_SealPhoto": _seal_b64(),          # 징수의무자 직인
    }

    # ── 정산명세 (2쪽) — wht_calc 로 법정 산식 계산 ──
    from wht_calc import compute
    deducs, unmapped = load_deducs(cur, t["emp_seq"], yy)
    persons = load_persons(cur, t["emp_seq"], yy)
    calc_in = dict(gross=pay + bonus,
                   prepaid_tax=inc.get("소득세", 0),
                   prepaid_local=inc.get("지방소득세", 0),
                   **persons, **deducs)
    r = compute(calc_in)

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
        "Data6_Amt26": _num(r[35]), "Data6_Amt27": _num(r[36]),
        "Data6_Amt34": _num(r[41]), "Data6_Amt40": _num(r[46]),
        "Data6_Amt41": _num(r[47]),
        "Data6_Amt52": _num(r[48]), "Data6_Amt53": _num(r[49]),
        # 57.자녀세액공제 — 인원/금액, 출산·입양
        "Data6_DeducChild": str(r["57cnt"]) if r["57cnt"] else "",
        "Data6_Amt60": _num(r[57]),
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
        "Data5_Tax_Deducted": _num(r["77tax"]) or "0",
        "Data5_ResidTax_Deducted": _num(r["77local"]) or "0",
        "Data5_SpecialTax_Deducted": "",
        "Data5_Tax_Spec": "", "Data5_ResidTax_Spec": "", "Data5_SpecialTax_Spec": "",
    })
    if unmapped:
        print("⚠️  미분류 공제 항목 (검토 필요):")
        for nm, a in unmapped:
            print(f"    {nm}: {a:,.0f}")
    return t, v


def render(cur, emp_no, yy):
    tpl = load_template(cur, yy)
    t, values = build_values(cur, emp_no, yy)
    # 반복행 펼치기 — 1쪽 근무처/소득명세, 3쪽 부양가족
    inc = load_income(cur, t["emp_seq"], yy)
    beg, end = load_period(cur, t["emp_seq"], yy)
    d2, d3, _ = build_income_rows(t, inc, beg, end, load_company(cur))
    tpl = _expand(tpl, "Data2", d2)
    tpl = _expand(tpl, "Data3", d3)
    # 비과세·감면 명세 — 해당 없어도 원본 서식처럼 빈 행을 채운다.
    # 행이 없으면 아래 '20.계' 행과 열 너비가 어긋난다.
    tpl = _expand(tpl, "Data4", [{}] * 14)
    tpl = expand_family_rows(tpl, load_family(cur, t["emp_seq"], yy))

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


def generate(emp_no, yy, as_pdf=True):
    """원천징수영수증 생성 → (bytes, 파일명)

    ERP 는 읽기만 한다. 주민등록번호는 암호화라 빈칸으로 나가고
    담당자가 채운다 (2026-08-06 합의).
    PDF 변환이 불가한 환경이면 HTML 로 돌려준다.
    """
    conn = _conn()
    cur = conn.cursor()
    try:
        html, t, filled, missing = render(cur, emp_no, yy)
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
    ap.add_argument("--out", help="저장 경로 (기본: 원천징수영수증_<이름>_<YY>.html)")
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
        out = args.out or f"원천징수영수증_{t['name']}_{args.yy}.html"
        open(out, "w", encoding="utf-8").write(html)
        print(f"생성: {out}  ({len(html):,}자)")
        print(f"토큰 채움 {len(filled)}개 / 미채움 {len(missing)}개")
        print("미채움(계산값 — ERP 발급본 대조로 채워 나갈 것):")
        for k in missing[:40]:
            print("  ", k)
        if len(missing) > 40:
            print(f"   … 외 {len(missing)-40}개")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
