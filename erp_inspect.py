#!/usr/bin/env python3
"""ERP 테이블 구조 및 샘플 데이터 확인 (1회용)"""
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

# 분석 대상 테이블
targets = ['_TDAEmpDate', '_TDAContact', '_TDAEmpIn', '_TDAEmpFile']

for tbl in targets:
    print(f'\n{"="*50}')
    print(f'테이블: {tbl}')
    try:
        cur.execute(f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='{tbl}' ORDER BY ORDINAL_POSITION")
        cols = [r[0] for r in cur.fetchall()]
        print(f'컬럼: {", ".join(cols)}')

        # 첫번째 유효 행 출력
        cur.execute(f"SELECT TOP 1 * FROM {tbl}")
        row = cur.fetchone()
        if row:
            print('샘플:')
            for col, val in zip(cols, row):
                if val is not None and str(val).strip():
                    print(f'  {col}: {repr(val)}')
        else:
            print('(데이터 없음)')
    except Exception as e:
        print(f'오류: {e}')

c.close()
