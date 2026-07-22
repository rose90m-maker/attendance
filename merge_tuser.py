#!/usr/bin/env python3
"""
tuser 중복 계정 통합 (1회용) — ERP 사번 기준

같은 ERP 사번에 tuser가 2개 있는 경우, 주 계정(KEEP)으로 데이터를 이관하고
보조 계정(DROP)을 삭제한다. 이름도 ERP 정식 표기로 맞춘다.

실행: docker exec attendance-app python3 /app/merge_tuser.py [--run]
      (--run 없으면 미리보기)
"""
import os, sys
from dotenv import load_dotenv

load_dotenv()
import pymysql

RUN = '--run' in sys.argv

# (설명, ERP사번, ERP정식이름, 유지할 id, 삭제할 id)
MERGES = [
    ('배옥림', '20051102', '배옥림', 46, 90005),
]

# tuser.id 참조 컬럼
REF_COLS = [
    ('wr_entries', 'user_id'),
    ('wr_group_members', 'user_id'),
    ('wr_group_reviewers', 'user_id'),
    ('wr_group_writers', 'user_id'),
    ('annual_leave', 'e_id'),
    ('leave_records', 'e_id'),
    ('app_users', 'e_id'),
    ('bus_members', 'user_id'),
    ('tenter', 'e_id'),
    ('tenter_erp', 'e_id'),
    ('lp_group_members', 'user_id'),
    ('lp_group_reviewers', 'user_id'),
]
# UNIQUE 제약이 있어 중복 시 이관 대신 삭제해야 하는 것
UNIQUE_TABLES = {('wr_group_members', 'user_id'), ('wr_group_reviewers', 'user_id'),
                 ('wr_group_writers', 'user_id'), ('lp_group_members', 'user_id'),
                 ('lp_group_reviewers', 'user_id'), ('annual_leave', 'e_id'),
                 ('app_users', 'e_id')}

ac = pymysql.connect(host=os.environ.get('DB_HOST', '192.168.100.11'),
                     port=int(os.environ.get('DB_PORT', 3307)),
                     user=os.environ.get('DB_USER', 'root'),
                     password=os.environ['DB_PASSWORD'],
                     database='attendance', charset='utf8mb4')
cur = ac.cursor()

# 백업
cur.execute("SHOW TABLES LIKE 'tuser_bak_merge'")
if not cur.fetchone():
    cur.execute("CREATE TABLE tuser_bak_merge AS SELECT * FROM tuser")
    ac.commit()
    print('백업 테이블 tuser_bak_merge 생성')
cur.execute("SELECT COUNT(*) FROM tuser_bak_merge")
print(f'백업 보유: {cur.fetchone()[0]}건\n')

cur.execute("""SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA='attendance'""")
existing = {(t, c) for t, c in cur.fetchall()}
refs = [(t, c) for t, c in REF_COLS if (t, c) in existing]

for label, empno, erp_name, keep, drop in MERGES:
    print(f'=== {label} (사번 {empno}) : id {drop} → {keep} 통합 ===')

    # 사번 검증
    cur.execute("SELECT id, name, idno FROM tuser WHERE id IN (%s,%s)", (keep, drop))
    found = {r[0]: (r[1], (r[2] or '').strip()) for r in cur.fetchall()}
    if keep not in found or drop not in found:
        print(f'   ⚠️ 계정 없음 (keep={keep in found}, drop={drop in found}) — 건너뜀\n')
        continue
    if found[keep][1] != empno or found[drop][1] != empno:
        print(f'   ⚠️ 사번 불일치 {found} — 건너뜀\n')
        continue
    print(f'   유지: id={keep} 현재이름="{found[keep][0]}" → "{erp_name}"로 수정')
    print(f'   삭제: id={drop} 이름="{found[drop][0]}"')

    for tbl, col in refs:
        cur.execute(f"SELECT COUNT(*) FROM `{tbl}` WHERE `{col}`=%s", (drop,))
        n = cur.fetchone()[0]
        if not n:
            continue
        if (tbl, col) in UNIQUE_TABLES:
            cur.execute(f"SELECT COUNT(*) FROM `{tbl}` WHERE `{col}`=%s", (keep,))
            k = cur.fetchone()[0]
            act = f'{n}건 삭제 (유지계정에 이미 {k}건 존재)' if k else f'{n}건 이관'
        else:
            act = f'{n}건 이관'
        print(f'      {tbl}.{col}: {act}')

    if RUN:
        for tbl, col in refs:
            cur.execute(f"SELECT COUNT(*) FROM `{tbl}` WHERE `{col}`=%s", (drop,))
            if not cur.fetchone()[0]:
                continue
            if (tbl, col) in UNIQUE_TABLES:
                cur.execute(f"SELECT COUNT(*) FROM `{tbl}` WHERE `{col}`=%s", (keep,))
                if cur.fetchone()[0]:
                    cur.execute(f"DELETE FROM `{tbl}` WHERE `{col}`=%s", (drop,))
                    continue
            cur.execute(f"UPDATE `{tbl}` SET `{col}`=%s WHERE `{col}`=%s", (keep, drop))
        # 이름 ERP 표기로 통일 후 보조계정 삭제
        cur.execute("UPDATE tuser SET name=%s WHERE id=%s", (erp_name, keep))
        cur.execute("DELETE FROM tuser WHERE id=%s", (drop,))
        # tenter 표시이름도 정정
        cur.execute("UPDATE tenter SET e_name=%s WHERE e_id=%s", (erp_name, keep))
        ac.commit()
        print('   ✅ 통합 완료')
    print()

if RUN:
    print('=== 검증 ===')
    for label, empno, erp_name, keep, drop in MERGES:
        cur.execute("SELECT id, name, idno FROM tuser WHERE idno=%s", (empno,))
        print(f'   사번 {empno}: {cur.fetchall()}')
        for tbl, col in [('tenter', 'e_id'), ('wr_entries', 'user_id')]:
            cur.execute(f"SELECT COUNT(*) FROM `{tbl}` WHERE `{col}`=%s", (keep,))
            print(f'      {tbl}: {cur.fetchone()[0]}건')
else:
    print('[미리보기] 실제 반영하려면 --run 을 붙여 실행하세요.')

ac.close()
