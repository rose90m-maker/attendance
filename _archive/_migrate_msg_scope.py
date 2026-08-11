#!/usr/bin/env python3
"""_migrate_msg_scope.py — 이름 UNIQUE 를 공용 기준(uk_name)으로 수렴

배포된 app_maria._msg_scope_index() 가 첫 API 호출 때 자동으로 하는 일을
그대로, 다만 **관찰 가능한 상태에서 미리** 실행한다. 사용자가 처음 클릭하는
순간에 DDL 이 실패하면 화면에서 오류로 드러나므로 그 전에 확인해 둔다.

멱등하다 — 이미 uk_name_owner 가 있으면 아무것도 하지 않는다.
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


def scope_index(table):
    cur.execute("""SELECT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s
                     AND NON_UNIQUE=0 AND INDEX_NAME<>'PRIMARY'
                   GROUP BY INDEX_NAME""", (table,))
    names = {r[0] for r in cur.fetchall()}
    if names == {"uk_name"}:
        print(f"  {table}: 이미 uk_name(name) — 건너뜀")
        return
    # 공용이라 이름이 목록의 식별자다. 같은 이름이 여럿이면 UNIQUE 를 못 건다.
    cur.execute(f"SELECT name, COUNT(*) FROM `{table}` "
                f"GROUP BY name HAVING COUNT(*)>1")
    dup = cur.fetchall()
    if dup:
        print(f"  {table}: ❌ 이름 중복 {len(dup)}건 — 손으로 정리 후 다시")
        for d in dup:
            print(f"      {d[0]!r} × {d[1]}")
        return
    for nm in names:
        cur.execute(f"ALTER TABLE `{table}` DROP INDEX `{nm}`")
        print(f"  {table}: 옛 UNIQUE `{nm}` 제거")
    cur.execute(f"ALTER TABLE `{table}` ADD UNIQUE KEY `uk_name` (`name`)")
    print(f"  {table}: ✅ uk_name(name) 생성")


for t in ("msg_favorites", "msg_templates"):
    cur.execute("SHOW TABLES LIKE %s", (t,))
    if not cur.fetchone():
        print(f"  {t}: 테이블 없음 — 첫 사용 때 앱이 만든다")
        continue
    scope_index(t)

c.commit()

print("\n== 이관 후 상태 ==")
for t in ("msg_favorites", "msg_templates"):
    cur.execute("SHOW TABLES LIKE %s", (t,))
    if not cur.fetchone():
        continue
    cur.execute(f"SHOW INDEX FROM `{t}` WHERE Non_unique=0")
    idx = {}
    for x in cur.fetchall():
        idx.setdefault(x[2], []).append(x[4])
    cur.execute(f"SELECT COUNT(*) FROM `{t}`")
    print(f"  {t}: {cur.fetchone()[0]}행 · {idx}")
c.close()
