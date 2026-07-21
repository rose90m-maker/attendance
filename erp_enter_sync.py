#!/usr/bin/env python3
"""
ERP 출입기록(TAEIN_TXPRWkCapsData) → attendance.tenter_erp 수집 + CAPS(tenter) 대조

병행 검증 단계: tenter는 그대로 두고, ERP 데이터를 별도 테이블에 모아 매일 비교한다.
차이가 임계치를 넘으면 태인 알림방으로 통보.

실행: docker exec attendance-app python3 /app/erp_enter_sync.py [--days N]
"""
import os, sys, logging, requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    handlers=[logging.StreamHandler()])
log = logging.getLogger('erp_enter')

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT = os.environ.get('TELEGRAM_CHAT_ID', '')

# ERP WkFileCode → tenter e_mode (동일 이벤트 4,500건 대조로 100% 검증됨)
CODE_MAP = {'1': '1', '2': '2', '5': '3'}   # 출근 / 퇴근 / 외출

# 차이가 이 값을 넘으면 알림
ALERT_THRESHOLD = 5


def tg(msg):
    if not TELEGRAM_TOKEN:
        return
    try:
        requests.post(f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
                      json={'chat_id': TELEGRAM_CHAT, 'text': msg}, timeout=5)
    except Exception:
        pass


def erp_conn():
    import pymssql
    return pymssql.connect(
        server=os.environ['ERP_DB_HOST'], port=int(os.environ.get('ERP_DB_PORT', 14233)),
        user=os.environ['ERP_DB_USER'], password=os.environ['ERP_DB_PASSWORD'],
        database=os.environ.get('ERP_DB_NAME', 'TAEIN'), charset='UTF-8')


def att_conn():
    import pymysql
    return pymysql.connect(
        host=os.environ.get('DB_HOST', '192.168.100.11'),
        port=int(os.environ.get('DB_PORT', 3307)),
        user=os.environ.get('DB_USER', 'root'), password=os.environ['DB_PASSWORD'],
        database='attendance', charset='utf8mb4')


def run(days=3):
    today = datetime.now().date()
    frm = (today - timedelta(days=days - 1)).strftime('%Y%m%d')
    to = today.strftime('%Y%m%d')
    log.info('ERP 출입기록 수집: %s ~ %s', frm, to)

    # ── 1) ERP에서 출입기록 + 사원정보 조회 ──
    ec = erp_conn()
    cur = ec.cursor()
    cur.execute("""
        SELECT d.WkDate, d.WkTime, d.CardId, d.WkFileCode, e.Empid, e.EmpName
        FROM TAEIN_TXPRWkCapsData d
        LEFT JOIN _TXPRWkEmpCard c ON d.CardId = c.CardId
        LEFT JOIN _TDAEmp e ON c.EmpSeq = e.EmpSeq
        WHERE d.WkDate BETWEEN %s AND %s
    """, (frm, to))
    erp_rows = []
    for wdate, wtime, card, code, empid, empname in cur.fetchall():
        erp_rows.append((
            (wdate or '').strip(), (wtime or '').strip(), (card or '').strip().upper(),
            CODE_MAP.get((code or '').strip(), ''),
            (empid or '').strip(), (empname or '').strip(),
        ))
    ec.close()
    log.info('ERP 조회: %d건', len(erp_rows))

    ac = att_conn()
    cur2 = ac.cursor()

    # ── 2) 사번 → tuser.id 매핑 ──
    cur2.execute("SELECT id, name, idno FROM tuser")
    by_idno = {}
    for uid, nm, idno in cur2.fetchall():
        if idno and str(idno).strip():
            by_idno[str(idno).strip()] = uid

    # ── 3) tenter_erp 적재 (중복은 무시) ──
    inserted = 0
    unmapped_cards = set()
    for wdate, wtime, card, mode, empid, empname in erp_rows:
        if not wdate or not wtime or not card:
            continue
        if not empid:
            unmapped_cards.add(card)
        eid = by_idno.get(empid)
        cur2.execute("""
            INSERT IGNORE INTO tenter_erp (e_date, e_time, e_card, e_mode, e_id, e_name, e_idno)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (wdate, wtime, card, mode, eid, empname, empid))
        inserted += cur2.rowcount
    ac.commit()
    log.info('tenter_erp 신규 적재: %d건 (미매핑 카드 %d개)', inserted, len(unmapped_cards))

    # ── 4) 날짜별 CAPS(tenter) vs ERP 대조 ──
    alerts = []
    for i in range(days):
        d = (today - timedelta(days=i)).strftime('%Y%m%d')
        # CAPS: 정상 인증(e_result='0') + 카드 있는 건만 (실패 로그 제외)
        cur2.execute("""SELECT e_card, e_time FROM tenter
                        WHERE e_date=%s AND e_result='0' AND e_card<>''""", (d,))
        caps = {(str(a or '').strip().upper(), str(b or '').strip()) for a, b in cur2.fetchall()}
        cur2.execute("SELECT e_card, e_time FROM tenter_erp WHERE e_date=%s", (d,))
        erp = {(str(a or '').strip().upper(), str(b or '').strip()) for a, b in cur2.fetchall()}
        if not caps and not erp:
            continue
        only_caps = caps - erp
        only_erp = erp - caps
        detail = f'CAPS만 {len(only_caps)}건 / ERP만 {len(only_erp)}건'
        cur2.execute("""
            INSERT INTO tenter_diff_log (check_date, caps_cnt, erp_cnt, only_caps, only_erp, detail)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE checked_at=NOW(), caps_cnt=VALUES(caps_cnt),
                erp_cnt=VALUES(erp_cnt), only_caps=VALUES(only_caps),
                only_erp=VALUES(only_erp), detail=VALUES(detail)
        """, (d, len(caps), len(erp), len(only_caps), len(only_erp), detail))
        log.info('%s CAPS=%d ERP=%d | %s', d, len(caps), len(erp), detail)
        # ERP에만 있는 건 = CAPS 동기화 누락 → 중요
        if len(only_erp) >= ALERT_THRESHOLD:
            alerts.append(f'· {d[:4]}-{d[4:6]}-{d[6:]} CAPS 누락 {len(only_erp)}건 '
                          f'(CAPS {len(caps)} / ERP {len(erp)})')
    ac.commit()
    ac.close()

    if alerts:
        tg('⚠️ 출입기록 CAPS/ERP 차이 감지\n\n' + '\n'.join(alerts) +
           '\n\nCAPS 동기화 누락 가능성을 확인해 주세요.')
        log.warning('알림 발송: %d일치', len(alerts))
    else:
        log.info('임계치 초과 없음 (알림 미발송)')


def weekly_report(days=7):
    """최근 N일 대조 결과를 요약해 태인 알림방으로 발송 (주간 리포트)"""
    ac = att_conn()
    cur = ac.cursor()
    frm = (datetime.now().date() - timedelta(days=days)).strftime('%Y%m%d')
    cur.execute("""SELECT check_date, caps_cnt, erp_cnt, only_caps, only_erp
                   FROM tenter_diff_log WHERE check_date >= %s
                   ORDER BY check_date""", (frm,))
    rows = cur.fetchall()
    ac.close()

    if not rows:
        log.info('주간 리포트: 대조 데이터 없음')
        return

    lines = [f'📊 출입기록 CAPS/ERP 주간 대조 ({len(rows)}일)', '']
    miss_days = 0
    total_miss = 0
    for d, caps, erp, oc, oe in rows:
        d = str(d)
        disp = f'{d[4:6]}/{d[6:]}'
        if oe > 0:
            miss_days += 1
            total_miss += oe
            lines.append(f'· {disp}  CAPS {caps} / ERP {erp}  ⚠️ 누락 {oe}건')
        else:
            lines.append(f'· {disp}  CAPS {caps} / ERP {erp}  ✅ 일치')

    lines.append('')
    if miss_days == 0:
        lines.append('✅ 전 기간 일치 — CAPS 정상 동작')
    else:
        lines.append(f'⚠️ {miss_days}일에서 CAPS 누락 총 {total_miss}건')
        lines.append('ERP 기준 전환을 검토해 주세요.')

    tg('\n'.join(lines))
    log.info('주간 리포트 발송: %d일 / 누락일 %d', len(rows), miss_days)


if __name__ == '__main__':
    days = 3
    if '--days' in sys.argv:
        i = sys.argv.index('--days')
        if i + 1 < len(sys.argv):
            days = int(sys.argv[i + 1])
    if '--weekly' in sys.argv:
        # 주간 리포트만 발송 (수집은 매일 작업이 담당)
        weekly_report(7)
    else:
        run(days)
