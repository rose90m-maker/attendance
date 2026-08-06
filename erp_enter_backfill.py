# -*- coding: utf-8 -*-
"""ERP 출입기록 과거분 tenter_erp 소급 적재 (알림·대조 없음)

erp_enter_sync.py 는 매일 3일치를 적재하면서 CAPS 대조 결과를 태인 알림방으로 보낸다.
과거 몇 달치를 한 번에 채울 때 그 알림이 같이 나가면 안 되므로, 적재만 하는 전용 스크립트를 둔다.
ERP 원본 TAEIN_TXPRWkCapsData 는 2021-07-08 부터 보관되어 있다.

실행:
    python3 erp_enter_backfill.py 20260101 20260630
"""
import sys
from datetime import datetime

from erp_enter_sync import CODE_MAP, att_conn, erp_conn


def backfill(frm, to):
    print(f"ERP 출입기록 소급 적재: {frm} ~ {to}")
    ec = erp_conn()
    cur = ec.cursor()
    cur.execute("""
        SELECT d.WkDate, d.WkTime, d.CardId, d.WkFileCode, e.Empid, e.EmpName
        FROM TAEIN_TXPRWkCapsData d
        LEFT JOIN _TXPRWkEmpCard c ON d.CardId = c.CardId
        LEFT JOIN _TDAEmp e ON c.EmpSeq = e.EmpSeq
        WHERE d.WkDate BETWEEN %s AND %s
    """, (frm, to))
    rows = cur.fetchall()
    ec.close()
    print(f"  ERP 조회: {len(rows):,}건")

    ac = att_conn()
    c2 = ac.cursor()
    c2.execute("SELECT id, name, idno FROM tuser")
    by_idno = {str(idno).strip(): uid
               for uid, nm, idno in c2.fetchall() if idno and str(idno).strip()}

    inserted = 0
    unmapped = set()
    for wdate, wtime, card, code, empid, empname in rows:
        wdate = (wdate or "").strip()
        wtime = (wtime or "").strip()
        card = (card or "").strip().upper()
        if not wdate or not wtime or not card:
            continue
        empid = (empid or "").strip()
        if not empid:
            unmapped.add(card)
        c2.execute("""
            INSERT IGNORE INTO tenter_erp (e_date, e_time, e_card, e_mode, e_id, e_name, e_idno)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (wdate, wtime, card, CODE_MAP.get((code or "").strip(), ""),
              by_idno.get(empid), (empname or "").strip(), empid))
        inserted += c2.rowcount
    ac.commit()

    c2.execute("SELECT COUNT(*), MIN(e_date), MAX(e_date) FROM tenter_erp")
    n, mn, mx = c2.fetchone()
    ac.close()
    print(f"  신규 적재 {inserted:,}건 (미매핑 카드 {len(unmapped)}개)")
    print(f"  tenter_erp 누적 {n:,}건 · {mn} ~ {mx}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    a, b = sys.argv[1], sys.argv[2]
    for v in (a, b):
        datetime.strptime(v, "%Y%m%d")   # 형식 검증
    backfill(a, b)
