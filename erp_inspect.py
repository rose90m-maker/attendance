#!/usr/bin/env python3
"""ERP HR 뷰 전화/주소 복호화 값 확정 조회 (1회용)"""
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

def q(sql, label=''):
    print(f'\n{"="*55}\n{label}\n{"-"*55}')
    try:
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        if cols: print(' | '.join(cols))
        for r in rows: print(r)
        if not rows: print('(결과 없음)')
    except Exception as e:
        print(f'오류: {e}')

# 1) _VWHREmpCodeInfo — 재직자 중 전화/주소 채워진 사람 (핵심!)
q("""SELECT TOP 10 EmpID, EmpName, Cellphone, Phone, Extension, AddrZip, Addr
     FROM _VWHREmpCodeInfo
     WHERE RetireDate >= '99991231'
       AND (Cellphone <> '' OR Addr <> '')""",
  "1) _VWHREmpCodeInfo 재직자 전화/주소 (값 있는 것만)")

# 2) 전체 재직자 중 전화 있는 사람 수 / 주소 있는 사람 수
q("""SELECT
        SUM(CASE WHEN Cellphone <> '' THEN 1 ELSE 0 END) AS 휴대폰있음,
        SUM(CASE WHEN Phone <> '' THEN 1 ELSE 0 END) AS 일반전화있음,
        SUM(CASE WHEN Addr <> '' THEN 1 ELSE 0 END) AS 주소있음,
        COUNT(*) AS 총재직자
     FROM _VWHREmpCodeInfo WHERE RetireDate >= '99991231'""",
  "2) 재직자 전화/주소 보유 통계")

# 3) _VXHREmpAddr — 주소 타입별 (도로명 500052002 우선)
q("""SELECT TOP 5 EmpSeq, ZipCode, Addr, SMAddressTypeSeq
     FROM _VXHREmpAddr WHERE Addr <> '' AND SMAddressTypeSeq = 500052002""",
  "3) _VXHREmpAddr 도로명주소 샘플")

c.close()
