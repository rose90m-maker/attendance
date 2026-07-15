#!/usr/bin/env python3
"""ERP 전화번호/주소 소스 심층 조사 (1회용)"""
import os
from dotenv import load_dotenv
load_dotenv()
import pymssql

c = pymssql.connect(
    server=os.environ['ERP_DB_HOST'],
    port=int(os.environ.get('ERP_DB_PORT', 14233)),
    user=os.environ['ERP_DB_USER'],
    password=os.environ['ERP_DB_PASSWORD'],
    database=os.environ.get('ERP_DB_NAME', 'TAEIN'),
    charset='UTF-8',
)
cur = c.cursor()

_out = open('/app/erp_inspect_result.txt', 'w', encoding='utf-8')
def p(s=''):
    print(s)
    _out.write(str(s) + '\n')

def q(sql, label=''):
    p(f'\n{"="*55}\n{label}\n{"-"*55}')
    try:
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        if cols:
            p(' | '.join(cols))
        for r in rows:
            p(r)
        if not rows:
            p('(결과 없음)')
    except Exception as e:
        p(f'오류: {e}')

# 1) 복호화 관련 함수/프로시저 존재 여부
q("""SELECT TOP 30 name, type_desc FROM sys.objects
     WHERE type IN ('FN','IF','TF','P')
       AND (name LIKE '%Decrypt%' OR name LIKE '%복호%' OR name LIKE '%Dec%'
            OR name LIKE '%Crypt%' OR name LIKE '%Phone%' OR name LIKE '%Cell%')
     ORDER BY name""", "1) 복호화/전화 관련 함수·프로시저")

# 2) 전화번호가 들어있을 법한 모든 컬럼 (전 테이블)
q("""SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
     WHERE COLUMN_NAME LIKE '%Phone%' OR COLUMN_NAME LIKE '%Cell%'
        OR COLUMN_NAME LIKE '%Tel%' OR COLUMN_NAME LIKE '%Mobile%'
        OR COLUMN_NAME LIKE '%HP%'
     ORDER BY TABLE_NAME""", "2) 전화번호 후보 컬럼 (전 테이블)")

# 3) 주소 후보 컬럼
q("""SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
     WHERE COLUMN_NAME LIKE '%Addr%' OR COLUMN_NAME LIKE '%주소%'
        OR COLUMN_NAME LIKE '%Zip%' OR COLUMN_NAME LIKE '%Post%'
     ORDER BY TABLE_NAME""", "3) 주소 후보 컬럼 (전 테이블)")

# 4) _TDAContact 에 재직자 전화번호 실제로 있는지 (평문?)
q("""SELECT TOP 10 e.Empid, e.EmpName, ct.ContactNumber, ct.Tel2, ct.FAX, ct.Email
     FROM _TDAEmp e JOIN _TDAContact ct ON e.ContactSeq = ct.ContactSeq
     WHERE e.IsInner='1' AND e.RetireDate>='99991231'""",
  "4) _TDAContact 재직자 연락처 샘플")

# 5) _TCOMAddress 컬럼 구조 + 샘플
q("""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
     WHERE TABLE_NAME='_TCOMAddress' ORDER BY ORDINAL_POSITION""",
  "5a) _TCOMAddress 컬럼")
q("""SELECT TOP 3 * FROM _TCOMAddress""", "5b) _TCOMAddress 샘플")

# 6) 사용자 정의 필드 (_TDAEmpUserDefine) — 회사가 커스텀 저장했을 수 있음
q("""SELECT TOP 5 * FROM _TDAEmpUserDefine""",
  "6) _TDAEmpUserDefine 샘플")

_out.close()
c.close()
print('\n\n>>> 결과 저장: /app/erp_inspect_result.txt')
