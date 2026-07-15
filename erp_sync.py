#!/usr/bin/env python3
"""
ERP(영림원) → attendance.employee_roster 자동 동기화
매일 17:30 NAS cron으로 실행: docker exec attendance-app python3 /app/erp_sync.py
"""
import os, logging, requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger('erp_sync')

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT  = os.environ.get('TELEGRAM_CHAT_ID', '')

def tg(msg):
    if not TELEGRAM_TOKEN:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT, 'text': msg, 'parse_mode': 'HTML'},
            timeout=5
        )
    except Exception:
        pass

def erp_conn():
    import pymssql
    return pymssql.connect(
        server=os.environ['ERP_DB_HOST'],
        port=int(os.environ.get('ERP_DB_PORT', 14233)),
        user=os.environ['ERP_DB_USER'],
        password=os.environ['ERP_DB_PASSWORD'],
        database=os.environ.get('ERP_DB_NAME', 'TAEIN'),
        charset='UTF-8',
    )

def att_conn():
    import pymysql
    return pymysql.connect(
        host=os.environ.get('DB_HOST', '192.168.100.11'),
        port=int(os.environ.get('DB_PORT', 3307)),
        user=os.environ.get('DB_USER', 'root'),
        password=os.environ['DB_PASSWORD'],
        database='attendance',
        charset='utf8mb4',
    )

def run():
    log.info('ERP 동기화 시작')
    now = datetime.now().strftime('%Y-%m-%d %H:%M')

    # ERP 재직자 조회
    ec = erp_conn()
    cur = ec.cursor()
    # RetireDate >= '99991231' = 퇴사 미정 (진짜 재직자)
    # IsInner='1'만으로는 과거 퇴직자가 포함되므로 RetireDate로 2차 필터
    cur.execute("""
        SELECT e.Empid, e.EmpName, d.DeptName, e.EntDate, e.RetireDate,
               e.EmpEngFirstName, e.EmpEngLastName,
               ei.BirthDate, ei.IsWoman, ei.Email, ei.Extension
        FROM _TDAEmp e
        LEFT JOIN _TDADept d ON e.DeptSeq = d.DeptSeq
        LEFT JOIN _TDAEmpIn ei ON e.EmpSeq = ei.EmpSeq AND ei.CompanySeq = 1
        WHERE e.IsInner = '1' AND e.RetireDate >= '99991231'
    """)
    erp_data = {}
    for r in cur.fetchall():
        no = (r[0] or '').strip()
        if no:
            hire = str(r[3] or '')
            if len(hire) == 8:
                hire = f'{hire[:4]}-{hire[4:6]}-{hire[6:]}'
            first = (r[5] or '').strip()
            last  = (r[6] or '').strip()
            name_en = f'{last} {first}'.strip() if (first or last) else ''
            birth = str(r[7] or '').strip()
            if len(birth) == 8:
                birth = f'{birth[:4]}-{birth[4:6]}-{birth[6:]}'
            gender = '여자' if str(r[8] or '0') == '1' else '남자'
            erp_data[no] = {
                'name': r[1] or '',
                'dept': r[2] or '',
                'hire_date': hire,
                'name_en': name_en,
                'birth_date': birth,
                'gender': gender,
                'email': str(r[9] or '').strip(),
                'internal_phone': str(r[10] or '').strip(),
            }
    ec.close()
    log.info('ERP 재직자: %d명', len(erp_data))

    # attendance 명부 조회
    ac = att_conn()
    cur2 = ac.cursor()
    cur2.execute("SELECT emp_no, name, dept, hire_date, name_en, birth_date, gender, email, internal_phone FROM employee_roster")
    att_data = {}
    for r in cur2.fetchall():
        no = (r[0] or '').strip()
        att_data[no] = {
            'name': r[1] or '', 'dept': r[2] or '', 'hire_date': str(r[3] or ''),
            'name_en': r[4] or '', 'birth_date': str(r[5] or ''), 'gender': r[6] or '',
            'email': r[7] or '', 'internal_phone': r[8] or '',
        }
    log.info('attendance 명부: %d명', len(att_data))

    erp_nos = set(erp_data)
    att_nos = set(att_data)

    inserted, updated = [], []

    # 신규 추가 (ERP에만 있는 사원)
    for no in erp_nos - att_nos:
        d = erp_data[no]
        cur2.execute("""
            INSERT INTO employee_roster
                (emp_no, name, dept, hire_date, work_status, name_en, birth_date, gender, email, internal_phone)
            VALUES (%s, %s, %s, %s, '재직', %s, %s, %s, %s, %s)
        """, (no, d['name'], d['dept'], d['hire_date'] or None,
              d['name_en'] or '', d['birth_date'] or None,
              d['gender'] or '', d['email'] or '', d['internal_phone'] or ''))
        inserted.append(f"{no} {d['name']} ({d['dept']})")

    # 정보 갱신 (이름 또는 부서 변경)
    for no in erp_nos & att_nos:
        e, a = erp_data[no], att_data[no]
        changes = {}
        if e['name'] and e['name'] != a['name']:
            changes['name'] = e['name']
        if e['dept'] and a['dept'] and e['dept'] != a['dept']:
            changes['dept'] = e['dept']
        if e['name_en'] and e['name_en'] != a.get('name_en', ''):
            changes['name_en'] = e['name_en']
        if e['birth_date'] and e['birth_date'] != a.get('birth_date', ''):
            changes['birth_date'] = e['birth_date']
        if e['gender'] and not a.get('gender'):
            changes['gender'] = e['gender']
        if e['email'] and not a.get('email'):
            changes['email'] = e['email']
        if e['internal_phone'] and not a.get('internal_phone'):
            changes['internal_phone'] = e['internal_phone']
        if changes:
            sets = ', '.join(f"{k}=%s" for k in changes)
            cur2.execute(
                f"UPDATE employee_roster SET {sets} WHERE emp_no=%s",
                (*changes.values(), no)
            )
            change_str = ' / '.join(f"{k}: {a[k]}->{v}" for k, v in changes.items())
            updated.append(f"{no} {e['name']} ({change_str})")

    ac.commit()
    ac.close()

    log.info('신규 추가: %d명, 정보 갱신: %d명', len(inserted), len(updated))

    # 동기화 결과 로그 DB 저장
    detail_lines = []
    if inserted:
        detail_lines.append(f'신규 {len(inserted)}명: ' + ', '.join(inserted[:5]) + ('...' if len(inserted) > 5 else ''))
    if updated:
        detail_lines.append(f'갱신 {len(updated)}명: ' + ', '.join(u.split(' | ')[0] for u in updated[:5]) + ('...' if len(updated) > 5 else ''))
    detail = ' / '.join(detail_lines) if detail_lines else '변경없음'

    log_conn = att_conn()
    log_cur = log_conn.cursor()
    log_cur.execute(
        "INSERT INTO erp_sync_log (inserted, updated, detail) VALUES (%s, %s, %s)",
        (len(inserted), len(updated), detail)
    )
    log_conn.commit()
    log_conn.close()

    if inserted or updated:
        lines = [f'[ERP 동기화] {now}']
        if inserted:
            lines.append(f'신규 추가 {len(inserted)}명:')
            lines += [f'  + {s}' for s in inserted[:10]]
            if len(inserted) > 10:
                lines.append(f'  ... 외 {len(inserted)-10}명')
        if updated:
            lines.append(f'정보 갱신 {len(updated)}명:')
            lines += [f'  * {s}' for s in updated[:10]]
            if len(updated) > 10:
                lines.append(f'  ... 외 {len(updated)-10}명')
        tg('\n'.join(lines))
        log.info('\n'.join(lines))
    else:
        log.info('변경사항 없음')

if __name__ == '__main__':
    run()
