#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""출입기록 기반 자동 근무보고서 46건 정리 (일회용)

2026-08-06 오전에 auto_work_report.py 가 출입기록을 근거로 만든 보고서를 지운다.
근무표기록관리 적재 방식이 '엑셀 근무표 기준'으로 바뀌면서 소스가 무효해졌다.
(임동훈 7/2 처럼 무급휴가인 날에 출입 태그가 있어 8시간으로 잡힌 건이 있다)

지우는 대상은 memo='출입기록 자동작성' 인 보고서와 그에 딸린 행뿐이다.
장용국이 직접 만든 하계휴가 2건(8/7·8/10)은 건드리지 않는다.

    python _cleanup_auto_work_report.py --dry-run
    python _cleanup_auto_work_report.py
"""
import argparse
import os
import sys

import pymysql

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

MARK = "출입기록 자동작성"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = pymysql.connect(
        host=os.environ.get("DB_HOST", "192.168.100.11"),
        port=int(os.environ.get("DB_PORT", 3307)),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ["DB_PASSWORD"],
        database="attendance", charset="utf8mb4", autocommit=False)
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, group_id, report_date FROM wr_reports WHERE memo=%s ORDER BY id", (MARK,))
        rows = cur.fetchall()
        if not rows:
            print("대상 없음")
            return 0
        ids = [r[0] for r in rows]
        print(f"대상 보고서 {len(ids)}건  #{ids[0]}~#{ids[-1]}")

        cur.execute("SELECT id, group_name FROM wr_groups")
        gname = dict(cur.fetchall())

        # 이 보고서들이 적재한 근무표 기록 — 같은 그룹·날짜·'보고서' 건만 지운다
        ph = ",".join(["%s"] * len(ids))
        cur.execute(f"""
            SELECT DISTINCT e.user_name, r.report_date, r.group_id
            FROM wr_entries e JOIN wr_reports r ON r.id = e.report_id
            WHERE e.report_id IN ({ph})
        """, ids)
        pairs = [(nm, rd, gname.get(g, "")) for nm, rd, g in cur.fetchall()]
        print(f"연결된 근무표 기록 후보 {len(pairs)}건")

        n_sr = 0
        for nm, rd, sheet in pairs:
            cur.execute("""DELETE FROM schedule_record
                           WHERE emp_name=%s AND work_date=%s AND sheet_name=%s
                             AND source_type='보고서' AND uploaded_by='자동'""",
                        (nm, rd, sheet))
            n_sr += cur.rowcount
        cur.execute(f"DELETE FROM wr_approval_log WHERE report_id IN ({ph})", ids)
        n_log = cur.rowcount
        cur.execute(f"DELETE FROM wr_entries WHERE report_id IN ({ph})", ids)
        n_ent = cur.rowcount
        cur.execute(f"DELETE FROM wr_reports WHERE id IN ({ph})", ids)
        n_rep = cur.rowcount

        print(f"삭제: 보고서 {n_rep} · 항목 {n_ent} · 결재로그 {n_log} · 근무표기록 {n_sr}")
        if args.dry_run:
            conn.rollback()
            print("[DRY-RUN] 롤백 — DB 변경 없음")
        else:
            conn.commit()
            print("커밋 완료")
    except Exception as e:
        conn.rollback()
        print(f"실패 — 롤백: {e}")
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
