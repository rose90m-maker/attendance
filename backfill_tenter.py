#!/usr/bin/env python3
"""
tenter 누락분 보정 (1회용) — 2026-07-01 ~ 07-02 CAPS 동기화 중단 구간 복구

tenter_erp(ERP 출입기록) 기준으로 tenter에 없는 건만 INSERT.
기존 데이터는 수정/삭제하지 않음. 백업: tenter_bak_20260701_02

실행: docker exec attendance-app python3 /app/backfill_tenter.py --run
      (--run 없으면 미리보기만)
"""
import os, sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
import pymysql

RUN = '--run' in sys.argv
TARGET_DAYS = ['20260701', '20260702']

ac = pymysql.connect(host=os.environ.get('DB_HOST', '192.168.100.11'),
                     port=int(os.environ.get('DB_PORT', 3307)),
                     user=os.environ.get('DB_USER', 'root'),
                     password=os.environ['DB_PASSWORD'],
                     database='attendance', charset='utf8mb4')
cur = ac.cursor()

# 백업 테이블 없으면 생성
cur.execute("SHOW TABLES LIKE 'tenter_bak_20260701_02'")
if not cur.fetchone():
    cur.execute("""CREATE TABLE tenter_bak_20260701_02 AS
                   SELECT * FROM tenter WHERE e_date IN ('20260701','20260702')""")
    ac.commit()
    print('백업 테이블 생성 완료')
cur.execute("SELECT COUNT(*) FROM tenter_bak_20260701_02")
print(f'백업 보유: {cur.fetchone()[0]}건 (tenter_bak_20260701_02)')

total = 0
now_str = datetime.now().strftime('%Y%m%d%H%M%S')

for day in TARGET_DAYS:
    cur.execute("SELECT e_card, e_time FROM tenter WHERE e_date=%s", (day,))
    have = {(str(a or '').strip().upper(), str(b or '').strip()) for a, b in cur.fetchall()}

    cur.execute("""SELECT e_time, e_card, e_mode, e_id, e_name, e_idno
                   FROM tenter_erp WHERE e_date=%s ORDER BY e_time""", (day,))
    missing = [r for r in cur.fetchall()
               if (str(r[1] or '').strip().upper(), str(r[0] or '').strip()) not in have]

    print(f'\n{day}: 기보유 {len(have)}건 / 추가대상 {len(missing)}건')
    for t, card, mode, eid, nm, idno in missing[:5]:
        print(f'   {t}  {nm or "-":8} {idno or "-":10} mode={mode}')
    if len(missing) > 5:
        print(f'   ... 외 {len(missing)-5}건')

    if RUN:
        for t, card, mode, eid, nm, idno in missing:
            cur.execute("""
                INSERT INTO tenter
                    (e_date, e_time, e_card, e_mode, e_id, e_name, e_idno,
                     g_id, e_group, e_user, e_type, e_result, e_etc, e_picture,
                     e_upmode, e_exyn, e_maskst, e_tempst, e_uptime)
                VALUES (%s,%s,%s,%s,%s,%s,%s,
                        3, 1, '0', '0', '0', '5', '0',
                        '1', 'Y', '0', '0', %s)
            """, (day, t, card, mode, eid, nm or '', idno or '', now_str))
            total += 1
        ac.commit()

if RUN:
    print(f'\n✅ 보정 완료: {total}건 추가')
    for day in TARGET_DAYS:
        cur.execute("""SELECT COUNT(*) FROM tenter
                       WHERE e_date=%s AND e_result='0' AND e_card<>''""", (day,))
        c1 = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM tenter_erp WHERE e_date=%s", (day,))
        c2 = cur.fetchone()[0]
        mark = '일치' if c1 == c2 else f'차이 {c1-c2:+d}'
        print(f'   {day}: tenter={c1} / ERP={c2}  → {mark}')
else:
    print('\n[미리보기] 실제 반영하려면 뒤에 --run 을 붙여 실행하세요.')

ac.close()
