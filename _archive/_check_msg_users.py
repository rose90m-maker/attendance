#!/usr/bin/env python3
"""_check_msg_users.py — 문자 보내기 메뉴를 쓸 수 있는 계정 조사 (읽기 전용)

즐겨찾기·문구 템플릿을 계정별로 나눌지 공용으로 둘지는 '실제로 몇 명이
쓰느냐'로 갈린다. admin 은 무조건 통과하고, 그 외에는 권한그룹의 msg_send
권한으로 정해진다 (_menu_perm).
"""
import os

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import pymysql

c = pymysql.connect(host=os.environ.get("DB_HOST", "192.168.100.11"),
                    port=int(os.environ.get("DB_PORT", "3307")),
                    user=os.environ.get("DB_USER", "root"),
                    password=os.environ["DB_PASSWORD"],
                    database=os.environ.get("DB_NAME", "attendance"),
                    charset="utf8mb4")
cur = c.cursor()


def cols(t):
    cur.execute("SHOW COLUMNS FROM `%s`" % t)
    return [r[0] for r in cur.fetchall()]


print("== app_users 계정 ==")
ac = cols("app_users")
sel = [x for x in ("id", "username", "user_id", "name", "role", "group_id",
                   "perm_group_id", "is_active", "active") if x in ac]
cur.execute(f"SELECT {', '.join('`'+x+'`' for x in sel)} FROM app_users ORDER BY id")
rows = cur.fetchall()
print("   " + " | ".join(sel))
admins = 0
for r in rows:
    d = dict(zip(sel, r))
    if str(d.get("role", "")).lower() == "admin":
        admins += 1
    print("   " + " | ".join(str(d.get(k, "")) for k in sel))
print(f"\n   전체 {len(rows)}개 계정 · admin {admins}개")

print("\n== 권한그룹의 msg_send 권한 ==")
for t in ("perm_groups", "perm_group_menus", "perm_menu", "group_menu_perms"):
    cur.execute("SHOW TABLES LIKE %s", (t,))
    if not cur.fetchone():
        continue
    cc = cols(t)
    print(f"\n   [{t}] 컬럼: {', '.join(cc)}")
    key = next((x for x in cc if "menu" in x.lower() and "key" in x.lower()), None)
    if key:
        cur.execute(f"SELECT * FROM `{t}` WHERE `{key}` LIKE %s", ("%msg%",))
        got = cur.fetchall()
        print(f"   msg 관련 행 {len(got)}개")
        for g in got[:10]:
            print("      " + " | ".join(str(x)[:20] for x in g))
    else:
        cur.execute(f"SELECT * FROM `{t}` LIMIT 8")
        for g in cur.fetchall():
            print("      " + " | ".join(str(x)[:20] for x in g))

print("\n== 즐겨찾기를 실제로 만든 계정 ==")
cur.execute("SELECT DISTINCT created_by FROM msg_favorites")
print("   msg_favorites:", [r[0] for r in cur.fetchall()])
cur.execute("SELECT DISTINCT created_by FROM msg_templates")
print("   msg_templates:", [r[0] for r in cur.fetchall()])
c.close()
