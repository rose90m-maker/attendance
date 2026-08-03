#!/usr/bin/env python3
"""ERP 웹 로그인 계정을 물어보기식으로 .env에 추가하는 1회용 도구.
값은 이 터미널에서 직접 입력되며 다른 곳으로 전송되지 않는다.
실행: python3 _env_erp_web.py
"""
import os
import getpass

ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

print("=" * 46)
print(" ERP 웹 로그인 계정 등록 (.env)")
print(" - K-System Ace (210.118.143.10:8100) 계정")
print(" - 휴가신청 화면 접근/저장 권한 필요")
print("=" * 46)

uid = input("ERP 웹 아이디: ").strip()
if not uid:
    print("아이디가 비어 있어 중단합니다.")
    raise SystemExit(1)
pwd = getpass.getpass("ERP 웹 비밀번호 (입력해도 화면엔 안 보임): ")
if not pwd:
    print("비밀번호가 비어 있어 중단합니다.")
    raise SystemExit(1)
pwd2 = getpass.getpass("비밀번호 다시 한 번: ")
if pwd != pwd2:
    print("두 입력이 달라 중단합니다. 다시 실행해 주세요.")
    raise SystemExit(1)

# 기존 키 있으면 교체, 없으면 추가
lines = []
if os.path.exists(ENV):
    with open(ENV, encoding="utf-8") as f:
        lines = [l.rstrip("\n") for l in f]
lines = [l for l in lines if not l.startswith(("ERP_WEB_ID=", "ERP_WEB_PWD="))]
lines += [f"ERP_WEB_ID={uid}", f"ERP_WEB_PWD={pwd}"]
with open(ENV, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print(f"\n✅ 저장 완료 → {ENV}")
print("   (ERP_WEB_ID / ERP_WEB_PWD 2개 키)")
print("   이 파일(_env_erp_web.py)은 지워도 됩니다.")
