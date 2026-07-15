#!/usr/bin/env python3
"""ERP HR 뷰에서 복호화된 전화/주소 탐색 (1회용)"""
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
    print(s); _out.write(str(s) + '\n')

def cols_of(tbl):
    cur.execute(f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='{tbl}' ORDER BY ORDINAL_POSITION")
    return [r[0] for r in cur.fetchall()]

def q(sql, label=''):
    p(f'\n{"="*55}\n{label}\n{"-"*55}')
    try:
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        if cols: p(' | '.join(cols))
        for r in rows: p(r)
        if not rows: p('(결과 없음)')
    except Exception as e:
        p(f'오류: {e}')

# 후보 HR 뷰들 — 컬럼 구조부터 파악
hr_views = ['_VXAPIHREmpInfo', '_VXHREmpAddr', '_VWHREmpAddress',
            '_VWHREmpInfoAddr', '_VWHREmpCodeInfo', '_VWHROrgEmpQueryGw',
            '_VWHREmpInfo', '_VXHREmpOut']

for v in hr_views:
    cols = cols_of(v)
    if cols:
        p(f'\n{"#"*55}\n[뷰] {v}\n컬럼: {", ".join(cols)}')
        # 전화/주소 관련 컬럼이 있으면 샘플 조회
        interesting = [c2 for c2 in cols if any(k in c2.lower() for k in
                       ['phone','cell','tel','mobile','addr','emp','name','id','zip'])]
        if interesting:
            sel = ', '.join(interesting[:12])
            q(f"SELECT TOP 3 {sel} FROM {v}", f"  ↳ {v} 샘플")
    else:
        p(f'\n[뷰] {v} — 존재하지 않음')

# _TDAEmpUserDefine 의 Serl 종류 (차량번호 외 다른 커스텀 필드?)
q("""SELECT DISTINCT Serl FROM _TDAEmpUserDefine ORDER BY Serl""",
  "_TDAEmpUserDefine Serl 종류")

_out.close(); c.close()
p('\n>>> 저장: /app/erp_inspect_result.txt')
