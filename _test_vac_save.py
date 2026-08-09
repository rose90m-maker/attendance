# -*- coding: utf-8 -*-
"""ERP 휴가신청 Save 진단 — 지창구(124) 연차 1일 2026-12-31

Save 래퍼는 #BIZ_OUT.Status != 0 이면 ROLLBACK 한다.
1차 시도에서 Status=71001 이 떠서 저장이 안 됐으므로,
이번엔 응답 전문을 찍어 Result/MessageType 메시지를 확인한다.

사용법:  python3 _test_vac_save.py
"""
import json
import os

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
BASE = os.environ["ERP_API_BASE"]
SVC = "VEN.Ylw.XPR.BssWkVacationApp"


def call(method, blocks, root_extra=None):
    root = {
        "certId": os.environ["ERP_API_CERT_ID"],
        "certKey": os.environ["ERP_API_CERT_KEY"],
        "dsnOper": os.environ["ERP_API_DSN_OPER"],
        "dsnBis": os.environ["ERP_API_DSN_BIS"],
        "companySeq": os.environ["ERP_API_COMPANY_SEQ"],
        "languageSeq": 1, "securityType": 0,
        # 빈 userId 로 보내면 @UserSeq=0 이라 _SWCOMBisProcCheck 가 Status=71001 을 세우고
        # 래퍼가 Status!=0 을 오류로 보고 ROLLBACK 한다. 실제 ERP 계정을 넣어야 한다.
        "userId": os.environ.get("ERP_API_USER_ID", "20040401"),   # 지창구 (UserSeq 5)
        "data": {"ROOT": blocks},
    }
    if root_extra:
        root.update(root_extra)
    r = requests.post(f"{BASE}/OpenApi/{SVC}/{method}",
                      headers={"Content-Type": "application/json"},
                      json={"ROOT": root}, timeout=30)
    return r.json()


# WkFrDate/WkToDate 가 있어야 잔여가 나온다 (없으면 0/YY=null)
BASE_Q = {"EmpSeq": 124, "WkItemSeq": 1001, "AppDate": "20260805",
          "WkFrDate": "20261231", "WkToDate": "20261231"}

print("[1] 기준선 — 지창구 연차 잔여")
print("   ", call("GetEtcUseDays", {"DataBlock1": [BASE_Q]})["DataBlock1"][0])

row = {
    "WorkingTag": "A",
    "EmpSeq": 124, "AppEmpSeq": 124,
    "WkItemSeq": 1001, "SMVacAppTermType": 5040004,
    "AppDate": "20260805",
    "VacFrDate": "20261231", "VacToDate": "20261231",
    "AppDays": 1, "DTCnt": 8, "MinCnt": 480,
    "VacReason": "휴가 API 연동 테스트",
    "IsCfm": "1",
    "Seq": 1, "VacDate": "20261231",
    "WkBegDate": "20261231", "BegTime": "0830",
    "WkEndDate": "20261231", "EndTime": "1730", "IsNextDate": "0",
    "WkTypeSeq": 1, "WkTeamSeq": -1, "WkFormSeq": -1, "DayTypeSeq": 1,
    "AppDay": 1, "SMYyOccurType": 3042001,
    "PossibleDays": 17, "PossibleDTCnt": 136, "PossibleMinCnt": 8160,
    "AllDTCnt": 8, "AllMinCnt": 480,
    "Status": 0, "Result": "",
}

# 71001 = '정보' 레벨. 래퍼는 Status!=0 이면 전부 롤백한다.
# _SWCOMBisProcCheck 가 @PgmSeq 를 받는데 지금까지 0 이 들어갔다.
# 휴가신청 화면 PgmSeq = 2720633 (FrmXPRWkVacationAppDtl)
PGM_SEQ = 2720633
CANDIDATES = [
    ("pgmSeq", {"pgmSeq": PGM_SEQ}),
    ("PgmSeq", {"PgmSeq": PGM_SEQ}),
    ("pgmSeq+workingTag", {"pgmSeq": PGM_SEQ, "workingTag": "A"}),
]

print("\n[2] Save — PgmSeq 조합별 시도 (Status=0 이 나오면 즉시 중단)")
saved = False
for label, extra in CANDIDATES:
    out = call("Save", {"DataBlock1": [dict(row)]}, root_extra=extra)
    if isinstance(out, dict) and "ErrorMessage" in out:
        print(f"   [{label}] ErrorMessage: "
              f"{json.dumps(out['ErrorMessage'], ensure_ascii=False)[:200]}")
        continue
    d = out.get("DataBlock1", [{}])[0]
    print(f"   [{label}] Status={d.get('Status')} "
          f"VacAppSeq={d.get('VacAppSeq')} Result={d.get('Result')!r}")
    if d.get("Status") in (0, None) and d.get("VacAppSeq"):
        print("   ✅ 저장 성공 — 이후 조합은 건너뜀")
        saved = True
        break
if not saved:
    print("   ⚠️ 모든 조합에서 저장 안 됨 (전부 롤백되었으므로 DB 변화 없음)")

print("\n[3] 저장 후 잔여 재조회 (17 → 16 이면 성공)")
print("   ", call("GetEtcUseDays", {"DataBlock1": [BASE_Q]})["DataBlock1"][0])
