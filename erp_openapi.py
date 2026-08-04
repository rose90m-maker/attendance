"""영림원 K-System OpenAPI 호출 모듈 — 휴가신청 연동용

태인관리시스템(결재) → ERP(확정 신청 등록) 단방향 연동.
ERP 쪽 휴가신청은 결재 단계가 없다(저장=확정, 2026-08-04 데이터로 확인).

엔드포인트 (2026-08-04 실측):
  POST http://210.118.143.10:8100/Angkor.Ylw.Common.HttpExecute/RestOutsideService.svc
       /OpenApi/IsStoredProcedure/{호출ID}
  · ERP 웹과 같은 호스트·포트 — 방화벽 추가 개방 불필요 (Mac/NAS 양쪽에서 도달 확인)
  · Content-Type: application/json
  · 인증은 헤더가 아니라 **본문 ROOT 안**에 certId/certKey 로 넣는다.

본문 형식 (ERP [Ksystem API 등록] > API호출 화면에서 확인):
  {"ROOT": {
      "certId": "...", "certKey": "...",
      "dsnOper": "taein_oper", "dsnBis": "taein_bis",
      "companySeq": "1", "languageSeq": 1,
      "securityType": 0, "userId": "",
      "data": {"ROOT": {"DataBlock1": [ {..항목..} ]}}
  }}

사용법:
  python erp_openapi.py --list                     # 등록된 호출ID 목록 (DB 조회)
  python erp_openapi.py --call OrgDeptQuery        # 실제 호출 (읽기 전용 SP 로 검증)
"""
import argparse
import json
import os
import sys

import requests
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))

ERP_API_BASE = os.environ.get(
    "ERP_API_BASE",
    "http://210.118.143.10:8100/Angkor.Ylw.Common.HttpExecute/RestOutsideService.svc",
)
# 자격증명은 .env 에서만 읽는다 (코드/로그에 값을 남기지 않는다).
ERP_API_CERT_ID = os.environ.get("ERP_API_CERT_ID", "")
ERP_API_CERT_KEY = os.environ.get("ERP_API_CERT_KEY", "")
# DSN 이름 — ERP API호출 화면 기준값
ERP_API_DSN_OPER = os.environ.get("ERP_API_DSN_OPER", "taein_oper")
ERP_API_DSN_BIS = os.environ.get("ERP_API_DSN_BIS", "taein_bis")
ERP_API_COMPANY_SEQ = os.environ.get("ERP_API_COMPANY_SEQ", "1")


def call_sp(call_id, datablocks=None, user_id="", timeout=30):
    """SP 공개설정 방식 API 호출.

    datablocks: {"DataBlock1": [{"항목ID": 값, ...}, ...]} 형태.
    서버가 DataBlock 을 XML 로 변환해 SP 의 @xmlDocument 로 넘긴다.
    """
    if not ERP_API_CERT_ID or not ERP_API_CERT_KEY:
        raise RuntimeError("ERP_API_CERT_ID / ERP_API_CERT_KEY 가 .env 에 없습니다")
    url = f"{ERP_API_BASE}/OpenApi/IsStoredProcedure/{call_id}"
    payload = {"ROOT": {
        "certId": ERP_API_CERT_ID,
        "certKey": ERP_API_CERT_KEY,
        "dsnOper": ERP_API_DSN_OPER,
        "dsnBis": ERP_API_DSN_BIS,
        "companySeq": ERP_API_COMPANY_SEQ,
        "languageSeq": 1,
        "securityType": 0,
        "userId": user_id,
        "data": {"ROOT": datablocks or {"DataBlock1": [{}]}},
    }}
    r = requests.post(url, headers={"Content-Type": "application/json"},
                      json=payload, timeout=timeout)
    r.raise_for_status()
    out = r.json()
    # 오류 응답 형태: {"ErrorMessage":[{"Status":"ERR","Result":"ERR|메시지|..."}]}
    err = out.get("ErrorMessage") if isinstance(out, dict) else None
    if err:
        raise RuntimeError(f"ERP API 오류: {err[0].get('Result', err)}")
    return out


def _list_registered():
    import pymssql
    c = pymssql.connect(
        server=os.environ["ERP_DB_HOST"], port=int(os.environ.get("ERP_DB_PORT", 14233)),
        user=os.environ["ERP_DB_USER"], password=os.environ["ERP_DB_PASSWORD"],
        database="TAEINCommon").cursor()
    c.execute("""SELECT KeyID, SqlScriptSeq, TimeOut, Remark
                 FROM _TCAProcedureExecute ORDER BY KeyID""")
    for k, s, t, rm in c.fetchall():
        print(f"  {str(k).strip():22s} script={s} timeout={t} {str(rm or '').strip()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--call", metavar="호출ID", help="API 호출 (읽기 전용 SP 로 검증)")
    ap.add_argument("--list", action="store_true", help="등록된 호출ID 목록")
    a = ap.parse_args()
    if a.list:
        _list_registered()
    elif a.call:
        try:
            out = call_sp(a.call)
            print(json.dumps(out, ensure_ascii=False, indent=2)[:3000])
        except Exception as e:
            print(f"실패: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        ap.print_help()
