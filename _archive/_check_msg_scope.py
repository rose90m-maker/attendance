#!/usr/bin/env python3
"""_check_msg_scope.py — 문자 즐겨찾기/문구 템플릿의 소유 범위 확인 (읽기 전용)

두 기능이 서로 다른 세션에서 만들어졌고 둘 다 계정 구분 없이 전체 공용이다.
계정별로 분리하기 전에 기존 행과 UNIQUE 제약을 확인한다 —
이름이 전체 UNIQUE 면 두 사람이 같은 이름('생산회의')을 쓸 수 없다.
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
for t in ("msg_favorites", "msg_templates"):
    cur.execute("SHOW TABLES LIKE %s", (t,))
    if not cur.fetchone():
        print(f"{t}: 아직 테이블 없음 (첫 사용 때 생성)")
        continue
    cur.execute(f"SELECT id, name, created_by FROM `{t}` ORDER BY id")
    rows = cur.fetchall()
    print(f"\n{t}: {len(rows)}행")
    for r in rows:
        print(f"   id={r[0]}  name={r[1]!r}  created_by={r[2]!r}")
    cur.execute(f"SHOW INDEX FROM `{t}` WHERE Non_unique=0")
    idx = {}
    for x in cur.fetchall():
        idx.setdefault(x[2], []).append(x[4])
    print(f"   UNIQUE 인덱스: {idx}")
c.close()
