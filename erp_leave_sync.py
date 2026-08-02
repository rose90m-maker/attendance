"""ERP(영림원) 연차 → attendance.annual_leave / leave_records 동기화

ERP 가 원본이다. 태인 시스템은 조회·집계만 한다.

  매핑    ERP _TDAEmp.Empid  ==  tuser.idno  →  tuser.id  ( = annual_leave.e_id )
  발생    _TXPRWkYyOccurDays (SMYyType=5043001)
          OccurDays(기본) + AddOccurDays(추가) + ExProbOccurDays(수습)
  차감    같은 행의 PileDays (전년도에서 당겨 쓴 양, 음수)
  사용    _TXPRWkVactionAppDtl 의 SUM(AppDay), IsCancel<>'1', 항목 1001(연차)·1009(반차)
  상세    같은 테이블의 날짜별 기록 → leave_records (1011 경조 포함)

주의
  · 일수는 반드시 _TXPRWkVactionAppDtl.AppDay 에서 온다.
    확정근태(_TXPRWkAbsence)는 AppDay 가 전부 NULL 이라 건수 대조용으로만 쓸 수 있다.
  · ERP 에 부여 데이터가 없는 사람은 건드리지 않는다. 재직자인데 ERP 등록이
    안 된 경우가 있어서, 지우면 화면에서 사라지고 연차를 적을 곳이 없어진다.
    그런 사람은 로그로만 남기고 인사팀이 ERP 에서 처리하게 한다.

사용법
  python erp_leave_sync.py              # 검증 모드 — 아무것도 쓰지 않고 결과만 출력
  python erp_leave_sync.py --apply      # 실제 반영 (반영 전 자동 백업)
  python erp_leave_sync.py --year 2025  # 대상 연도 지정 (기본: 올해)
"""
import argparse
import datetime
import os
import sys

import pymssql
import pymysql
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))

VAC_ITEMS = (1001, 1009)          # 연차 · 반차 — 연차 사용량으로 집계
DTL_ITEMS = (1001, 1009, 1011)    # + 경조 — 날짜별 기록에는 남긴다
ITEM_NAME = {1001: "연차", 1009: "반차", 1011: "경조"}
GRANT_TYPE = 5043001              # SMYyType — 같은 연도에 다른 유형 행이 또 있다

# 연차를 관리하지 않는 부서 (tuser.company 코드) — 동기화 대상에서 뺀다
EXCLUDE_COMPANIES = ("0007000000000000",)   # 경영기획팀


def erp_conn():
    return pymssql.connect(
        server=os.environ["ERP_DB_HOST"],
        port=int(os.environ.get("ERP_DB_PORT", 14233)),
        user=os.environ["ERP_DB_USER"],
        password=os.environ["ERP_DB_PASSWORD"],
        database=os.environ.get("ERP_DB_NAME", "TAEIN"),
    )


def att_conn():
    return pymysql.connect(
        host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", 3307)),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        db=os.environ["DB_NAME"], charset="utf8mb4",
    )


def fetch_erp(year):
    """ERP 에서 발생 / 사용 / 날짜별 상세를 읽어 온다"""
    yy = str(year)
    c = erp_conn()
    cur = c.cursor()

    cur.execute("""
        SELECT e.Empid,
               SUM(ISNULL(o.OccurDays,0) + ISNULL(o.AddOccurDays,0)
                   + ISNULL(o.ExProbOccurDays,0)),
               SUM(ISNULL(o.PileDays,0))
          FROM _TXPRWkYyOccurDays o JOIN _TDAEmp e ON e.EmpSeq = o.EmpSeq
         WHERE o.Yy = %s AND o.SMYyType = %d
         GROUP BY e.Empid
    """, (yy, GRANT_TYPE))
    grant = {str(r[0]).strip(): (float(r[1] or 0), float(r[2] or 0)) for r in cur.fetchall()}

    cur.execute("""
        SELECT e.Empid, d.VacDate, d.WkItemSeq, d.AppDay
          FROM _TXPRWkVactionAppDtl d JOIN _TDAEmp e ON e.EmpSeq = d.EmpSeq
         WHERE ISNULL(d.IsCancel,'0') <> '1' AND LEFT(d.VacDate,4) = %s
           AND d.WkItemSeq IN (1001, 1009, 1011)
         ORDER BY e.Empid, d.VacDate
    """, (yy,))
    detail = [(str(r[0]).strip(), str(r[1]).strip(), int(r[2]), float(r[3] or 0))
              for r in cur.fetchall()]

    # 아직 연차를 안 쓴 신규 입사자도 화면에 나와야 한다
    cur.execute("SELECT Empid, EmpName FROM _TDAEmp WHERE RetireDate = '99991231'")
    active = {str(r[0]).strip(): (r[1] or "").strip() for r in cur.fetchall()}

    c.close()
    return grant, detail, active


def load_map(cur):
    """tuser.idno(= ERP 사번) → tuser.id. 사번이 겹치면 매핑하지 않는다.

    연차를 관리하지 않는 부서는 아예 뺀다.
    """
    ph = ",".join(["%s"] * len(EXCLUDE_COMPANIES))
    cur.execute("SELECT id, name, idno FROM tuser "
                "WHERE idno IS NOT NULL AND idno <> '' "
                "  AND IFNULL(company,'') NOT IN (%s)" % ph, EXCLUDE_COMPANIES)
    by_idno = {}
    for tid, name, idno in cur.fetchall():
        by_idno.setdefault(str(idno).strip(), []).append((tid, name))
    return {k: v[0] for k, v in by_idno.items() if len(v) == 1}, \
           {k: v for k, v in by_idno.items() if len(v) > 1}


def backup(cur, year, stamp):
    """되돌릴 수 있게 원본을 통째로 복사해 둔다"""
    made = []
    for tbl in ("annual_leave", "leave_records"):
        bak = "_bak_%s_%s" % (tbl, stamp)
        cur.execute("DROP TABLE IF EXISTS `%s`" % bak)
        cur.execute("CREATE TABLE `%s` AS SELECT * FROM `%s`" % (bak, tbl))
        cur.execute("SELECT COUNT(*) FROM `%s`" % bak)
        made.append((bak, cur.fetchone()[0]))
    return made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 반영한다 (기본은 검증 모드)")
    ap.add_argument("--year", type=int, default=datetime.date.today().year)
    args = ap.parse_args()
    year = args.year

    grant, detail, active = fetch_erp(year)
    conn = att_conn()
    cur = conn.cursor()
    id_of, ambiguous = load_map(cur)

    # ── 사람별로 정리 ────────────────────────────────────────────────
    used, months, rows = {}, {}, {}
    for empid, vacdate, item, day in detail:
        if item in VAC_ITEMS:
            used[empid] = used.get(empid, 0.0) + day
            mm = int(vacdate[4:6])
            months.setdefault(empid, [0.0] * 12)[mm - 1] += day
        rows.setdefault(empid, []).append((vacdate, ITEM_NAME[item]))

    # 부여·사용이 있는 사람 + 재직자 전원. 재직자를 넣어야 신규 입사자도 화면에 나온다.
    with_data = set(grant) | set(used)
    targets = sorted(with_data | set(active))
    mapped = {e: id_of[e] for e in targets if e in id_of}
    unmapped = [e for e in targets if e not in id_of]

    cur.execute("""SELECT e_id, e_name, generated, total, used, deduct_prev, IFNULL(memo,'')
                     FROM annual_leave WHERE year=%s""", (year,))
    before, manual = {}, set()
    for r in cur.fetchall():
        before[r[0]] = (r[1], float(r[2] or 0), float(r[3] or 0), float(r[4] or 0), float(r[5] or 0))
        # memo 가 '수기' 로 시작하면 사람이 직접 넣은 값이라 건드리지 않는다.
        # ERP 에 아직 안 올라간 연차를 계획서 기준으로 넣어 둔 경우가 있다.
        if str(r[6]).startswith("수기"):
            manual.add(r[0])
    eids = {v[0] for v in mapped.values()}
    outside = [(e, before[e][0]) for e in before if e not in eids]

    ph_e = ",".join(["%s"] * len(EXCLUDE_COMPANIES))
    cur.execute("SELECT id FROM tuser WHERE IFNULL(company,'') IN (%s)" % ph_e, EXCLUDE_COMPANIES)
    excluded_eids = {r[0] for r in cur.fetchall()}
    outside = [o for o in outside if o[0] not in excluded_eids]

    cur.execute("SELECT COUNT(*) FROM leave_records WHERE LEFT(leave_date,4)=%s", (str(year),))
    lr_before = cur.fetchone()[0]

    print("=" * 78)
    print("■ %d년 ERP 연차 동기화 %s" % (year, "" if args.apply else "(검증 모드 — 쓰지 않음)"))
    print("  대상 %d명 (부여·사용 있음 %d명 + 재직자 %d명) → 매핑 %d명 / 미매핑 %d명 / 사번중복 %d건"
          % (len(targets), len(with_data), len(active), len(mapped), len(unmapped), len(ambiguous)))
    print("  발생 합계 %.1f일 / 사용 합계 %.1f일 / 날짜별 기록 %d건"
          % (sum(g[0] for g in grant.values()), sum(used.values()), len(detail)))
    print("  annual_leave 현재 %d명 → 갱신 %d명, 신규 %d명"
          % (len(before), len([e for e in mapped.values() if e[0] in before]),
             len([e for e in mapped.values() if e[0] not in before])))
    print("  leave_records %d년 %d건 → %d건 (%+d)"
          % (year, lr_before, len(detail), len(detail) - lr_before))

    if unmapped:
        print("\n  · tuser 에서 사번을 못 찾은 ERP 사원 %d명: %s" % (len(unmapped), unmapped[:10]))
    if ambiguous:
        print("  · 사번이 겹쳐 건너뛴 항목: %s" % list(ambiguous)[:5])
    if outside:
        print("\n  · ERP 에 %d년 연차 자료가 없어 그대로 두는 사람 %d명 — ERP 등록 필요:"
              % (year, len(outside)))
        for eid, nm in sorted(outside, key=lambda x: x[1] or ""):
            print("      %-8s (e_id=%s)" % (nm or "?", eid))

    changed = []
    for empid, (tid, nm) in mapped.items():
        if tid in manual or (empid not in with_data and tid in before):
            continue                      # 건드리지 않는 사람은 미리보기에서도 뺀다
        g, pile = grant.get(empid, (0.0, 0.0))
        u = used.get(empid, 0.0)
        old = before.get(tid)
        if old is None or abs(old[1] - g) > 0.001 or abs(old[3] - u) > 0.001 \
                or abs(old[4] - pile) > 0.001:
            changed.append((nm, tid, old, g, pile, u))
    print("\n  · 값이 바뀌는 사람: %d명" % len(changed))
    for nm, tid, old, g, pile, u in sorted(changed, key=lambda x: -abs(
            (x[2][3] if x[2] else 0) - x[5]))[:12]:
        o = "신규" if old is None else "발생 %.1f 사용 %.1f 차감 %.1f" % (old[1], old[3], old[4])
        print("      %-8s e_id=%-5s %-30s → 발생 %.1f 사용 %.1f 차감 %.1f (사용가능 %.1f)"
              % (nm or "?", tid, o, g, u, pile, pile + g))

    if not args.apply:
        print("\n  검증 모드입니다. 반영하려면 --apply 를 붙여 다시 실행하세요.")
        conn.close()
        return

    # ── 반영 ─────────────────────────────────────────────────────────
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    made = backup(cur, year, stamp)
    print("\n  백업 생성:", ", ".join("%s(%d행)" % b for b in made))

    skipped = 0
    for empid, (tid, nm) in mapped.items():
        g, pile = grant.get(empid, (0.0, 0.0))
        u = used.get(empid, 0.0)
        mo = months.get(empid, [0.0] * 12)

        # ERP 에 부여도 사용도 없는 사람은 이미 있는 행을 덮어쓰지 않는다.
        # 재직자인데 ERP 등록이 안 된 경우가 있어서, 0 으로 밀면 화면에서 연차가 사라진다.
        # 행 자체가 없을 때만 만들어 준다 (신규 입사자가 목록에 보이게).
        if empid not in with_data and tid in before:
            skipped += 1
            continue
        # 수기로 넣어 둔 값은 덮어쓰지 않는다 (memo 가 '수기…')
        if tid in manual:
            skipped += 1
            continue

        cur.execute("""
            INSERT INTO annual_leave
                   (e_id, e_name, year, total, used, deduct_prev, generated,
                    m1,m2,m3,m4,m5,m6,m7,m8,m9,m10,m11,m12)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                   e_name=VALUES(e_name), total=VALUES(total), used=VALUES(used),
                   deduct_prev=VALUES(deduct_prev), generated=VALUES(generated),
                   m1=VALUES(m1),m2=VALUES(m2),m3=VALUES(m3),m4=VALUES(m4),
                   m5=VALUES(m5),m6=VALUES(m6),m7=VALUES(m7),m8=VALUES(m8),
                   m9=VALUES(m9),m10=VALUES(m10),m11=VALUES(m11),m12=VALUES(m12)
        """, (tid, nm or "", year, pile + g, u, pile, g, *mo))

    # 날짜별 기록은 해당 연도 · 매핑된 사람 것만 갈아끼운다
    target_eids = [e for e in eids if e not in manual]
    if target_eids:
        ph = ",".join(["%s"] * len(target_eids))
        cur.execute("DELETE FROM leave_records WHERE LEFT(leave_date,4)=%%s AND e_id IN (%s)" % ph,
                    [str(year)] + target_eids)
    ins = 0
    for empid, (tid, _nm) in mapped.items():
        if tid in manual:
            continue
        for vacdate, kind in rows.get(empid, []):
            cur.execute("""INSERT INTO leave_records (e_id, leave_date, leave_type, status, memo)
                           VALUES (%s,%s,%s,'승인','ERP')""", (tid, vacdate, kind))
            ins += 1
    conn.commit()

    cur.execute("""SELECT COUNT(*), SUM(generated), SUM(used), SUM(deduct_prev), SUM(total)
                     FROM annual_leave WHERE year=%s""", (year,))
    r = cur.fetchone()
    print("  반영 완료 — annual_leave %d명 / 발생 %.1f / 사용 %.1f / 차감 %.1f / 사용가능 %.1f"
          % (r[0], float(r[1] or 0), float(r[2] or 0), float(r[3] or 0), float(r[4] or 0)))
    print("  건드리지 않은 사람: %d명 (ERP 자료 없음 + 수기 입력 %d명)" % (skipped, len(manual)))
    print("  leave_records %d년 %d건 기록" % (year, ins))
    print("\n  되돌리려면:")
    for bak, _n in made:
        tbl = bak.split("_bak_")[1].rsplit("_", 2)[0]
        print("    TRUNCATE %s; INSERT INTO %s SELECT * FROM %s;" % (tbl, tbl, bak))
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
