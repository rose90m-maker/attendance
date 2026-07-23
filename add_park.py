#!/usr/bin/env python3
"""
박승규(사번 20260701) 시스템 등록 (1회용)

출입카드 미발급자 → 근무표(근무보고서) 기반으로 근태 관리.
카드번호는 비워두고, 카드 발급 시 cardnum만 채우면 됨.

실행: docker exec attendance-app python3 /app/add_park.py [--run]
"""
import os, sys
from dotenv import load_dotenv

load_dotenv()
import pymysql

RUN = '--run' in sys.argv

NAME = '박승규'
EMPNO = '20260701'
NEW_ID = 4445                      # tuser/tenter 모두 미사용 확인됨
COMPANY = '0005000000000000'       # 전자생산 (SMT 동료와 동일)
GROUP_NAME = 'SMT'

ac = pymysql.connect(host=os.environ.get('DB_HOST', '192.168.100.11'),
                     port=int(os.environ.get('DB_PORT', 3307)),
                     user=os.environ.get('DB_USER', 'root'),
                     password=os.environ['DB_PASSWORD'],
                     database='attendance', charset='utf8mb4')
cur = ac.cursor()

# 사전 검증
cur.execute("SELECT id, name FROM tuser WHERE id=%s", (NEW_ID,))
if cur.fetchone():
    print(f'⚠️ id={NEW_ID} 이미 사용중 — 중단'); sys.exit(1)
cur.execute("SELECT id, name FROM tuser WHERE idno=%s", (EMPNO,))
dup = cur.fetchone()
if dup:
    print(f'⚠️ 사번 {EMPNO} 이미 등록됨 {dup} — 중단'); sys.exit(1)
cur.execute("SELECT name, dept, hire_date FROM employee_roster WHERE emp_no=%s", (EMPNO,))
er = cur.fetchone()
if not er or er[0] != NAME:
    print(f'⚠️ 명부 확인 실패 {er} — 중단'); sys.exit(1)
cur.execute("SELECT id FROM wr_groups WHERE group_name=%s", (GROUP_NAME,))
grp = cur.fetchone()
if not grp:
    print(f'⚠️ 그룹 {GROUP_NAME} 없음 — 중단'); sys.exit(1)
gid = grp[0]

print(f'등록 대상: {NAME} (사번 {EMPNO}, 명부부서 {er[1]}, 입사 {er[2]})')
print(f'  tuser  : id={NEW_ID}, company={COMPANY} (전자생산), 카드=없음')
print(f'  그룹   : {GROUP_NAME}(id={gid}) 멤버 추가')
print(f'  ※ 출입카드 미발급 → 근무보고서로 근태 관리\n')

if RUN:
    cur.execute("""INSERT INTO tuser (id, name, idno, company, cardnum, reg_date)
                   VALUES (%s,%s,%s,%s,'',%s)""",
                (NEW_ID, NAME, EMPNO, COMPANY,
                 str(er[2]).replace('-', '') if er[2] else ''))
    cur.execute("""INSERT IGNORE INTO wr_group_members (group_id, user_id, user_name)
                   VALUES (%s,%s,%s)""", (gid, NEW_ID, NAME))
    ac.commit()
    print('✅ 등록 완료\n')
    cur.execute("SELECT id,name,idno,company,cardnum FROM tuser WHERE id=%s", (NEW_ID,))
    print(f'   tuser: {cur.fetchone()}')
    cur.execute("""SELECT m.user_id,m.user_name,g.group_name FROM wr_group_members m
                   JOIN wr_groups g ON m.group_id=g.id WHERE m.user_id=%s""", (NEW_ID,))
    print(f'   그룹 : {cur.fetchone()}')
    cur.execute("SELECT COUNT(*) FROM wr_group_members WHERE group_id=%s", (gid,))
    print(f'   {GROUP_NAME} 멤버 총 {cur.fetchone()[0]}명')
else:
    print('[미리보기] 실제 반영하려면 --run 을 붙여 실행하세요.')

ac.close()
