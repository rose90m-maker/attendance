# -*- coding: utf-8 -*-
"""증명서(재직·경력) PDF 생성

ERP 의 재직증명서 양식(PrtFormSeq=39, RptXHRBasCertificateService)은 클라이언트
리포트 프로그램이라 파일로 받아올 수 없다. 그래서 2026-07 발급본 실물과 같은
레이아웃을 여기서 그리고, 값은 사원명부(employee_roster)와 회사정보로 채운다.

⚠️ ERP 발급본에는 주민등록번호와 주소 우편번호가 찍히지만 우리는 채울 수 없다.
   ERP `_VWHREmpCodeInfo.ResidId` 는 암호화되어 빈 문자열로 나오고 `AddrZip` 은
   NULL 이다. 값을 넘기지 않으면 빈칸으로 남는다.

공문서관리(document_management)에서 담당자가 '완료' 처리하면 자동 생성되고,
요청한 직원이 그대로 내려받는다.
"""
import io
import os
import re
from datetime import date, datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

# ── 회사 정보 ─────────────────────────────────────────────────────
# ERP _TCACompany(CompanySeq=1) + 2026-07 발급본 실물에서 확인한 값
COMPANY = {
    "name": "(주)태인",
    "address": "(28358) 충청북도 청주시 흥덕구 직지대로 243 (지동동)",
    "ceo_title": "대표이사",
    "ceo": "이인정, 이상현",
}

# 자동 생성이 가능한 문서종류 (나머지는 담당자가 직접 첨부한다)
AUTO_DOC_TYPES = ("재직증명서", "경력증명서")

# 직인 이미지 — 파일을 놓으면 서명줄 '(인)' 자리에 자동으로 찍힌다.
# 배경이 투명한 PNG 를 권장한다 (없으면 '(인)' 글자가 그대로 나온다).
STAMP_PATH = os.environ.get(
    "CERT_STAMP_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "cert_stamp.png"),
)
STAMP_SIZE = 1.9 * cm

# 한글 폰트는 reportlab 내장 CID 폰트(HYGothic-Medium)를 쓴다.
# 시스템에 TTF 를 설치할 필요가 없어 컨테이너 이미지를 건드리지 않는다.
# 다른 글꼴을 쓰고 싶으면 CERT_FONT_PATH 로 TTF 경로를 주면 그쪽이 우선한다.
FONT_NAME = "CertKR"
_CID_FONT = "HYGothic-Medium"
_font_ready = None      # 실제로 등록된 폰트 이름


def _ensure_font():
    """한글 폰트를 한 번만 등록하고 그 이름을 돌려준다."""
    global _font_ready
    if _font_ready:
        return _font_ready
    ttf = os.environ.get("CERT_FONT_PATH", "")
    if ttf and os.path.exists(ttf):
        pdfmetrics.registerFont(TTFont(FONT_NAME, ttf))
        _font_ready = FONT_NAME
    else:
        pdfmetrics.registerFont(UnicodeCIDFont(_CID_FONT))
        _font_ready = _CID_FONT
    return _font_ready


def _style(size=10.5, align=TA_LEFT, leading=None):
    return ParagraphStyle(
        f"c{size}{align}", fontName=_ensure_font(), fontSize=size,
        leading=leading or size * 1.5, alignment=align, wordWrap="CJK",
    )


def _fmt_kdate(v):
    """'20260506' / '2026-05-06' / date → '2026년 05월 06일'"""
    if not v:
        return ""
    if isinstance(v, (date, datetime)):
        return f"{v.year}년 {v.month:02d}월 {v.day:02d}일"
    s = re.sub(r"\D", "", str(v))
    if len(s) != 8 or s == "99991231":
        return ""
    return f"{s[:4]}년 {s[4:6]}월 {s[6:8]}일"


def _to_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = re.sub(r"\D", "", str(v or ""))
    if len(s) != 8 or s == "99991231":
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _tenure(hire, upto, retire=None):
    """'2026년 05월 06일 ~ 2026년 07월 02일 (0년 1개월)'

    ERP 발급본과 같이 재직 중이면 발급일까지로 끊고 기간을 괄호에 적는다.
    """
    beg = _to_date(hire)
    if not beg:
        return ""
    end = _to_date(retire) or upto
    months = (end.year - beg.year) * 12 + (end.month - beg.month)
    if end.day < beg.day:
        months -= 1
    months = max(0, months)
    return (f"{_fmt_kdate(beg)} ~ {_fmt_kdate(end)} "
            f"({months // 12}년 {months % 12}개월)")


def build_certificate(doc_type, emp, purpose="", issue_no="", issue_date=None,
                      task="", resid_id="", zip_code=""):
    """증명서 PDF 생성 → BytesIO

    emp: {name, name_en, dept, position, hire_date, address, retire_date}
    """
    font = _ensure_font()
    issue_date = issue_date or date.today()
    title = doc_type if doc_type in AUTO_DOC_TYPES else "재직증명서"

    val = _style(10.5, TA_LEFT)        # 값 칸
    ctr = _style(11, TA_CENTER)
    ctr_s = _style(9.5, TA_CENTER)
    ctr_b = _style(12, TA_CENTER)

    # 라벨은 Paragraph 로 만들지 않고 문자열 그대로 넣는다.
    # Paragraph 는 HTML 파서라 연속 공백을 하나로 줄여 버려서 '성     명' 같은
    # 관공서 표기를 못 쓴다. &nbsp; 로 벌렸더니 CID 폰트에서 U+00A0 가 점으로
    # 찍혔다 (2026-08-06 발급본). 문자열 셀은 공백이 그대로 살아 있다.
    def V(t):
        return Paragraph(t or "", val)

    addr = emp.get("address", "") or ""
    if zip_code:
        addr = f"({zip_code}) {addr}"

    # 서명 줄 — '대표이사 / 이름 / (인)' 을 벌려 놓는다.
    # 한 Paragraph 에 공백으로 벌리면 HTML 파서가 공백을 줄여 버려서 표로 배치한다.
    # 직인 이미지(STAMP_PATH)가 있으면 (인) 자리에 찍는다.
    stamp = None
    if STAMP_PATH and os.path.exists(STAMP_PATH):
        stamp = Image(STAMP_PATH, width=STAMP_SIZE, height=STAMP_SIZE)
        stamp.hAlign = "LEFT"
    sign = Table(
        [[Paragraph(COMPANY["ceo_title"], _style(12, TA_CENTER)),
          Paragraph(COMPANY["ceo"], _style(12, TA_CENTER)),
          stamp if stamp else Paragraph("(인)", _style(12, TA_CENTER))]],
        colWidths=[3.4 * cm, 5.0 * cm, 2.2 * cm],
    )
    sign.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (2, 0), (2, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    sign.hAlign = "CENTER"

    # 하단 서명부 — 한 셀 안에 여러 줄
    foot = [
        Spacer(1, 0.9 * cm),
        Paragraph(f"상기와 같이 {'재직하고 있음' if title == '재직증명서' else '근무하였음'}을 증명함.", ctr),
        Spacer(1, 0.9 * cm),
        Paragraph(_fmt_kdate(issue_date), ctr),
        Spacer(1, 1.4 * cm),
        Paragraph(COMPANY["name"], ctr_b),
        Spacer(1, 0.5 * cm),
        Paragraph(COMPANY["address"], ctr_s),
        Spacer(1, 0.5 * cm),
        sign,
    ]

    # 라벨 문구·공백은 ERP 발급본 그대로다
    data = [
        ["  ".join(title), "", "", ""],
        ["성     명", V(f"(한글) {emp.get('name', '')}"),
         "주민등록번호", V(resid_id)],
        ["", V(f"(영문) {emp.get('name_en', '') or ''}"),
         "입   사   일", V(_fmt_kdate(emp.get("hire_date")))],
        ["부     서", V(emp.get("dept", "")),
         "직         위", V(emp.get("position", ""))],
        ["현 주 소", V(addr), "", ""],
        ["재직기간", V(_tenure(emp.get("hire_date"), issue_date,
                            emp.get("retire_date"))), "", ""],
        ["담당업무", V(task), "", ""],
        ["용     도", V(purpose or ""), "", ""],
        [foot, "", "", ""],
    ]

    table = Table(
        data,
        colWidths=[2.7 * cm, 6.3 * cm, 2.9 * cm, 5.1 * cm],
        rowHeights=[2.4 * cm] + [1.15 * cm] * 7 + [9.6 * cm],
    )
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -2), 0.8, colors.black),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.black),
        ("SPAN", (0, 0), (3, 0)),      # 제목
        ("FONTNAME", (0, 0), (0, 0), font),
        ("FONTSIZE", (0, 0), (0, 0), 20),
        ("ALIGN", (0, 0), (0, 0), "CENTER"),
        ("SPAN", (0, 1), (0, 2)),      # 성명 세로병합 (한글/영문)
        ("SPAN", (1, 4), (3, 4)),      # 현주소
        ("SPAN", (1, 5), (3, 5)),      # 재직기간
        ("SPAN", (1, 6), (3, 6)),      # 담당업무
        ("SPAN", (1, 7), (3, 7)),      # 용도
        ("SPAN", (0, 8), (3, 8)),      # 하단 서명부
        # 라벨 칸(문자열 셀)의 글꼴·정렬은 여기서 준다
        ("FONTNAME", (0, 1), (0, 7), font),
        ("FONTNAME", (2, 1), (2, 3), font),
        ("FONTSIZE", (0, 1), (0, 7), 10.5),
        ("FONTSIZE", (2, 1), (2, 3), 10.5),
        ("ALIGN", (0, 1), (0, 7), "CENTER"),
        ("ALIGN", (2, 1), (2, 3), "CENTER"),
        ("VALIGN", (0, 0), (-1, -2), "MIDDLE"),
        ("VALIGN", (0, 8), (3, 8), "TOP"),
        ("ALIGN", (0, 8), (3, 8), "CENTER"),   # 서명줄 표를 가운데로
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2.5 * cm, rightMargin=2.5 * cm,
        topMargin=2.0 * cm, bottomMargin=1.5 * cm,
        title=f"{title} - {emp.get('name', '')}", author=COMPANY["name"],
    )
    story = [
        Paragraph(f"제 {issue_no} 호" if issue_no else "", _style(10, TA_LEFT)),
        Spacer(1, 0.35 * cm),
        table,
    ]
    doc.build(story)
    buf.seek(0)
    return buf


def safe_filename(doc_type, emp_name, issue_date=None):
    d = (issue_date or date.today()).strftime("%Y%m%d")
    name = re.sub(r"[^\w가-힣]", "", emp_name or "")
    return f"{doc_type}_{name}_{d}.pdf"
