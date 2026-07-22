#!/usr/bin/env python3
"""
tuser.id 정합성 보정 (1회용) — 신입 4명

문제: 출입기록(tenter)은 e_id 334~338로 들어오는데,
      tuser에는 90002~90008로 등록되어 조인 실패 → 부서별 출근현황 등에서 누락.

조치: tuser.id를 tenter e_id 값으로 변경하고, 해당 id를 참조하는
      wr_entries / wr_group_members / tenter_erp 등도 함께 갱신.

실행: docker exec attendance-app python3 /app/fix_tuser_id.py [--run]
      (--run 없으면 미리보기만)
"""
import os, sys
from dotenv import load_dotenv

load_dotenv()
import pymysql

RUN = '--run' in sys.argv

# (이름, ERP사번, 현재 tuser.id, 변경할 id = tenter e_id)
TARGETS = [
    ('성승현', '20260402', 90002, 334),
    ('김수정', '20260501', 90006, 336),
    ('김시은', '20260502', 90007, 337),
    ('황우민', '20260503', 90008, 338),
]

# tuser.id를 참조하는 테이블·컬럼
REF_COLS = [
    ('wr_entries', 'user_id'),
    ('wr_group_members', 'user_id'),
    ('wr_group_reviewers', 'user_id'),
    ('wr_group_writers', 'user_id'),
    ('annual_leave', 'e_id'),
    ('leave_records', 'e_id'),
    ('app_users', 'e_id'),
    ('bus_members', 'user_id'),
    ('tenter_erp', 'e_id'),
    ('lp_group_members', 'user_id'),
    ('lp_group_reviewers', 'user_id'),
]

ac = pymysql.connect(host=os.environ.get('DB_HOST', '192.168.100.11'),
                     port=int(os.environ.get('DB_PORT', 3307)),
                     user=os.environ.get('DB_USER', 'root'),
                     password=os.environ['DB_PASSWORD'],
                     database='attendance', charset='utf8mb4')
cur = ac.cursor()

# 백업
cur.execute("SHOW TABLES LIKE 'tuser_bak_idfix'")
if not cur.fetchone():
    cur.execute("CREATE TABLE tuser_bak_idfix AS SELECT * FROM tuser")
    ac.commit()
    print('백업 테이블 tuser_bak_idfix 생성')
cur.execute("SELECT COUNT(*) FROM tuser_bak_idfix")
print(f'백업 보유: {cur.fetchone()[0]}건\n')

# 존재하는 테이블만 추림
cur.execute("""SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA='attendance'""")
existing = {(t, c) for t, c in cur.fetchall()}
refs = [(t, c) for t, c in REF_COLS if (t, c) in existing]

total = 0
for name, empno, old_id, new_id in TARGETS:
    print(f'=== {name} (사번 {empno}): tuser.id {old_id} → {new_id} ===')

    # 대상 id 자리가 비어있는지 확인
    cur.execute("SELECT id, name FROM tuser WHERE id=%s", (new_id,))
    occupied = cur.fetchone()
    if occupied:
        print(f'   ⚠️ id={new_id} 이미 사용중 {occupied} — 건너뜀\n')
        continue
    cur.execute("SELECT id, name, idno FROM tuser WHERE id=%s", (old_id,))
    src = cur.fetchone()
    if not src:
        print(f'   ⚠️ tuser id={old_id} 없음 — 건너뜀\n')
        continue
    if (src[2] or '').strip() != empno:
        print(f'   ⚠️ 사번 불일치 (tuser={src[2]} / 기대={empno}) — 건너뜀\n')
        continue

    # 참조 건수 표시
    for tbl, col in refs:
        cur.execute(f"SELECT COUNT(*) FROM `{tbl}` WHERE `{col}`=%s", (old_id,))
        n = cur.fetchone()[0]
        if n:
            print(f'   {tbl}.{col}: {n}건 갱신 예정')

    if RUN:
        # tuser.id 변경 먼저 (PK)
        cur.execute("UPDATE tuser SET id=%s WHERE id=%s", (new_id, old_id))
        for tbl, col in refs:
            cur.execute(f"UPDATE `{tbl}` SET `{col}`=%s WHERE `{col}`=%s", (new_id, old_id))
        ac.commit()
        total += 1
        print(f'   ✅ 변경 완료')
    print()

if RUN:
    print(f'✅ 총 {total}명 보정 완료\n')
    # 검증
    cur.execute("""SELECT COUNT(DISTINCT t.e_id) FROM tenter t JOIN tuser u ON t.e_id=u.id
                   WHERE t.e_date=DATE_FORMAT(NOW(),'%Y%m%d') AND t.e_mode='1'""")
    print(f'   오늘 출근자 (tuser 조인 성공): {cur.fetchone()[0]}명')
    cur.execute("""SELECT COUNT(DISTINCT e_id) FROM tenter
                   WHERE e_date=DATE_FORMAT(NOW(),'%Y%m%d') AND e_mode='1' AND e_id>=0""")
    print(f'   오늘 출근자 (전체)          : {cur.fetchone()[0]}명')
else:
    print('[미리보기] 실제 반영하려면 --run 을 붙여 실행하세요.')

ac.close()
