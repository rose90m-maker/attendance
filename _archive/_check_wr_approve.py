#!/usr/bin/env python3
"""_check_wr_approve.py — 근무보고서 결재가 안 되는 원인 찾기 (읽기 전용)

process_wr_request() 는 단계(step)별로 권한을 본다.

    대기      → step 1 (작성자/멤버/writer)  → '작성완료'
    작성완료   → step 2 (검토자)             → '검토'
    검토      → step 3 (2차검토자)          → '결재'
    (final_step 이 1이나 2면 그 단계에서 바로 '결재')

권한은 **tuser.id(=세션 e_id)** 로 조회한다. wr_group_reviewers.user_id 에
다른 값(app_users.id, 사번 등)이 들어가 있으면 화면에는 검토자로 보이는데
결재 버튼은 "권한이 없습니다" 로 막힌다 — 가장 흔한 원인이다.

이 스크립트는 그 사람의 실제 권한과, 지금 걸려 있는 보고서의 상태를 나란히
보여 준다. DB 는 읽기만 한다.

사용:
  python3 _archive/_check_wr_approve.py --name 김기성
  python3 _archive/_check_wr_approve.py --name 김기성 --group 12
"""
import argparse
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

import pymysql

ap = argparse.ArgumentParser()
ap.add_argument("--name", required=True)
ap.add_argument("--group", type=int, default=0)
ap.add_argument("--days", type=int, default=30, help="최근 N일 보고서만")
args = ap.parse_args()

MARIA = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", "3307")),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ["DB_PASSWORD"],
    "db": os.environ.get("DB_NAME", "attendance"),
    "charset": "utf8mb4",
}

STEP_OF = {"대기": 1, "작성완료": 2, "검토": 3}


def head(t):
    print(f"\n{'=' * 76}\n  {t}\n{'=' * 76}")


conn = pymysql.connect(**MARIA)
cur = conn.cursor()

# ── ① 그 사람의 계정 식별자들 ────────────────────────────────
head(f"① '{args.name}' 의 계정 식별자")
cur.execute("SELECT id, name FROM tuser WHERE name=%s", (args.name,))
tusers = cur.fetchall()
print(f"  tuser (세션 e_id 로 쓰이는 값): {tusers or '없음'}")
try:
    cur.execute("""SELECT id, user_id, name, role FROM app_users
                   WHERE name=%s OR user_id=%s""", (args.name, args.name))
    print(f"  app_users (로그인 계정): {cur.fetchall() or '없음'}")
except Exception as e:
    print(f"  app_users 조회 실패: {str(e)[:80]}")

if not tusers:
    print("\n  ⚠️ tuser 에 이름이 없다 — 결재 권한 조회 자체가 안 된다.")
    print("     동명이인이거나 이름 표기(공백 등)가 다를 수 있다.")
tuser_ids = [r[0] for r in tusers]

# ── ② 그룹별 권한 ────────────────────────────────────────────
head("② 근무보고서 그룹 권한 (tuser.id 기준)")
if tuser_ids:
    ph = ",".join(["%s"] * len(tuser_ids))
    cur.execute(f"""SELECT g.id, g.group_name, g.final_step,
                           GROUP_CONCAT(DISTINCT r.step ORDER BY r.step) AS steps
                    FROM wr_groups g
                    LEFT JOIN wr_group_reviewers r
                      ON r.group_id=g.id AND r.user_id IN ({ph})
                    GROUP BY g.id, g.group_name, g.final_step
                    ORDER BY g.id""", tuple(tuser_ids))
    rows = cur.fetchall()
    print(f"  {'그룹':>5} {'이름':<18}{'최종단계':>8}  검토자 단계   멤버  작성자")
    print("  " + "-" * 66)
    for gid, gname, fstep, steps in rows:
        cur.execute(f"SELECT COUNT(*) FROM wr_group_members "
                    f"WHERE group_id=%s AND user_id IN ({ph})",
                    tuple([gid] + tuser_ids))
        is_mem = cur.fetchone()[0] > 0
        try:
            cur.execute(f"SELECT COUNT(*) FROM wr_group_writers "
                        f"WHERE group_id=%s AND user_id IN ({ph})",
                        tuple([gid] + tuser_ids))
            is_wr = cur.fetchone()[0] > 0
        except Exception:
            is_wr = False
        if not (steps or is_mem or is_wr):
            continue
        print(f"  {gid:>5} {(gname or '')[:17]:<18}{fstep:>8}  "
              f"{steps or '-':<12}{'O' if is_mem else '-':^6}{'O' if is_wr else '-':^6}")

# ── ③ 검토자 테이블에 '다른 id' 로 들어가 있지 않은지 ────────
head("③ wr_group_reviewers 에 이 이름이 다른 식별자로 들어가 있는가")
cur.execute("""SELECT r.group_id, g.group_name, r.step, r.user_id,
                      (SELECT name FROM tuser WHERE id=r.user_id) AS tuser_name
               FROM wr_group_reviewers r
               LEFT JOIN wr_groups g ON g.id=r.group_id
               ORDER BY r.group_id, r.step""")
allrev = cur.fetchall()
mine = [r for r in allrev if r[3] in tuser_ids]
orphan = [r for r in allrev if r[4] is None]
print(f"  전체 검토자 등록 {len(allrev)}건 · 그중 이 사람 {len(mine)}건")
if orphan:
    print(f"\n  ⚠️ tuser 에 없는 user_id 로 등록된 검토자 {len(orphan)}건 —"
          f" 이들은 절대 결재할 수 없다:")
    for gid, gname, step, uid, _ in orphan[:15]:
        print(f"      그룹 {gid}({gname}) step{step}  user_id={uid} ← tuser 에 없음")
else:
    print("  tuser 에 없는 user_id 로 등록된 검토자: 없음")

# ── ④ 지금 걸려 있는 보고서 ──────────────────────────────────
head(f"④ 최근 {args.days}일 · 결재 안 끝난 보고서")
sql = """SELECT r.id, r.group_id, g.group_name, r.report_date, r.status, g.final_step
         FROM wr_reports r LEFT JOIN wr_groups g ON g.id=r.group_id
         WHERE r.status <> '결재'
           AND r.report_date >= DATE_FORMAT(DATE_SUB(NOW(), INTERVAL %s DAY), '%%Y%%m%%d')"""
p = [args.days]
if args.group:
    sql += " AND r.group_id=%s"
    p.append(args.group)
sql += " ORDER BY r.report_date DESC, r.id DESC LIMIT 40"
cur.execute(sql, p)
pend = cur.fetchall()
if not pend:
    print("  없음")
print(f"  {'보고서':>7} {'그룹':<16}{'날짜':<10}{'상태':<8}{'필요단계':>8}  이 사람 처리 가능?")
print("  " + "-" * 74)
for rid, gid, gname, rdate, st, fstep in pend:
    need = STEP_OF.get(st)
    cur.execute(f"""SELECT step FROM wr_group_reviewers
                    WHERE group_id=%s AND user_id IN ({','.join(['%s']*len(tuser_ids))})"""
                if tuser_ids else "SELECT 1 WHERE 0", tuple([gid] + tuser_ids))
    steps = {r[0] for r in cur.fetchall()} if tuser_ids else set()
    if tuser_ids:
        cur.execute(f"SELECT COUNT(*) FROM wr_group_members WHERE group_id=%s "
                    f"AND user_id IN ({','.join(['%s']*len(tuser_ids))})",
                    tuple([gid] + tuser_ids))
        if cur.fetchone()[0]:
            steps.add(1)
    ok = need in steps
    why = "" if ok else (f"step{need} 권한 없음 (가진 것: {sorted(steps) or '없음'})"
                         if need else f"상태 '{st}' 는 결재 대상이 아님")
    print(f"  {rid:>7} {(gname or '')[:15]:<16}{rdate:<10}{st:<8}{('step'+str(need)) if need else '-':>8}"
          f"  {'✅' if ok else '❌ ' + why}")

conn.close()
print("""
읽는 법
  ②에 그 그룹이 아예 없으면 → 검토자로 등록이 안 된 것
  ③에 'tuser 에 없는 user_id' 로 나오면 → 잘못된 식별자로 등록된 것 (가장 흔함)
  ④에서 필요단계와 가진 단계가 다르면 → 순서상 아직 그 사람 차례가 아닌 것
""")
