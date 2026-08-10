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
    "근로소득세액공제": 55,
    "공제대상자녀(세액공제)": 57,
    # 61~63 번 칸은 「공제대상금액」과 「세액공제액」이 따로 있다.
    # 세액공제액만 매핑했더니 대상금액 칸이 비었다 — 김미선 2025 의
    # 의료비 대상금액 939,500 이 그랬다 (2026-08-10).
    "보장성보험(세액공제)": 61,
    "보장성보험료(대상금액)": "61obj",
    "의료비(세액공제)": 62,
    "의료비(대상금액)": "62obj",
    "교육비(세액공제)": 63,
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
    inc = load_income(cur, t["emp_seq"], yy)
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
        # 건강보험은 급여대장 합계(국민건강보험+정산분)가 아니라 33.㉮ 의 '공제금액'을 쓴다.
        # ERP 발급본이 하단 영수란에도 공제금액을 찍기 때문이다.
        # 예전에는 정산분까지 더해 2,367,230 이 나왔고 ERP 는 2,347,130 이라,
        # 같은 장 안에서 건강보험료가 두 값으로 보였다 (2026-08-10 지창구 2025 대조).
        "Data5_MedCurAmt": _num(deducs.get("health", 0)),
        "Data5_MedTotAmt": _num(deducs.get("health", 0)),
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
    calc_in = dict(gross=pay + bonus,
                   prepaid_tax=inc.get("소득세", 0),
                   prepaid_local=inc.get("지방소득세", 0),
                   **persons, **deducs)
    r = compute(calc_in)
    # ERP 가 이미 계산해 둔 값으로 덮어쓴다 (있는 항목만).
    # 이렇게 하면 공제 조합이 어떻든 ERP 발급본과 구조적으로 일치한다.
    _diff = apply_erp_result(r, load_erp_result(cur, t["emp_seq"], yy))
    if _diff:
        print(f"  ℹ️  ERP 값으로 보정 {len(_diff)}건 "
              f"(계산기와 달랐던 항목): "
              + ", ".join(str(k) for k, _o, _n in _diff[:8]))

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
    beg, end = load_period(cur, t["emp_seq"], yy)
    d2, d3, _ = build_income_rows(t, inc, beg, end, load_company(cur))
    tpl = _expand(tpl, "Data2", d2)
    tpl = _expand(tpl, "Data3", d3)
    # 비과세·감면 명세(Ⅱ) — 해당 없어도 원본처럼 빈 행을 채운다.
    # ⚠️ Ⅰ영역은 왼쪽에 세로 레이블 셀(rowspan=13)이 있어 행마다 28열인데
    #    Data4_repeat 에는 그 셀이 없어 27열이다. 그대로 두면 표가 한 칸씩 밀린다.
    #    → 첫 행에 레이블 셀을 끼워 넣는다 (빈행 + 20 + 20-1 을 덮는다).
    BLANK = 14
    tpl = _expand(tpl, "Data4", [{}] * BLANK)
    tpl = _insert_section2_label(tpl, BLANK + 2)
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
