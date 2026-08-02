"""ERP(영림원) 인사자료 → attendance.hr_* 동기화

ERP 가 원본이다. 태인 시스템은 조회·집계만 한다. 연차(erp_leave_sync.py)와 같은 방식이다.

  사원      _TDAEmp + _TDAEmpIn + _TDADept  → hr_employees
            + _TDAEmpUserDefine(차량번호)
  발령      _THRAdmOrdEmp                   → hr_orders
  부서이동  _THRAdmMoveDeptHist             → hr_dept_moves
  휴직      _THRAdmEmpRest                  → hr_rests
  증명서    _THRBasCertificate              → hr_certificates

주의
  · 주민번호(_TDAEmp.ResidID)·전화번호는 영림원이 앱 레벨에서 암호화해 저장한다.
    복호화할 방법이 없으므로 아예 가져오지 않는다.
    생년월일은 _TDAEmpIn.BirthDate 에 평문으로 따로 있어 이것만 쓴다 (재직 161명 중 160명).
  · 직급(PosSeq)은 전원 0 이라 쓰지 않는다. 부서만 쓴다 (직급은 명부관리에 있다).
  · 차량번호는 _TDAEmpUserDefine 의 사용자 정의 항목에 있다.
    항목 이름표는 _TCOMUserDefine 에 TableName='_TDAEmp', TitleSerl=1000001, Title='차량번호'.
    값에 '없음'·'서울' 처럼 차량번호가 아닌 것이 섞여 있어 형태를 보고 거른다.
  · 사번(Empid)이 없는 사람은 가져오지 않는다. 우리 직원이 아니다 —
    2026-08-02 급여명세서로 확인했다. ERP 재직 161명 중 급여를 받는 사람은 160명이고,
    나머지 한 명(박상선)은 사번도 없고 명부관리에도 없는 업체 개발자다.
  · 표 전체를 매번 갈아끼운다. 자료가 8천 행 남짓이라 증분이 필요 없다.

사용
  python erp_hr_sync.py            # 검증 모드 — 아무것도 쓰지 않고 결과만 출력
  python erp_hr_sync.py --apply    # 실제 반영
"""
import argparse
import datetime
import os
import re

import pymssql
import pymysql
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))

ACTIVE = "99991231"          # 재직자의 RetireDate
SEX = {"1010001": "남", "1010002": "여"}   # _TDAEmpIn.SMSexSeq
CAR_SERL = 1000001                        # _TDAEmpUserDefine 의 '차량번호' 항목
CAR_RE = re.compile(r"^\s*\d{2,3}\s*[가-힣]\s*\d{4}\s*$")   # 12가3456 / 123 가 4567


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


DDL = [
    """CREATE TABLE IF NOT EXISTS hr_employees (
        emp_seq     INT PRIMARY KEY,
        empid       VARCHAR(20),
        name        VARCHAR(50),
        dept_seq    INT,
        dept_name   VARCHAR(60),
        ent_date    CHAR(8),
        retire_date CHAR(8),
        birth_date  CHAR(8),
        sex         VARCHAR(4),          -- 남 / 여 (ERP 코드 1010001/1010002 를 옮긴 값)
        car_no      VARCHAR(20),         -- ERP 사용자 정의 항목 '차량번호' (형태가 맞는 것만)
        is_active   TINYINT NOT NULL DEFAULT 0,
        t_uid       INT NULL,
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX ix_active (is_active),
        INDEX ix_ent (ent_date),
        INDEX ix_birth (birth_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    # 같은 사람이 같은 날짜·OrdSeq 로 여러 건 있을 수 있어 ERP 원래 키(IntSeq)까지 넣는다
    """CREATE TABLE IF NOT EXISTS hr_orders (
        emp_seq   INT NOT NULL,
        int_seq   INT NOT NULL,
        ord_seq   INT NOT NULL,
        ord_date  CHAR(8) NOT NULL,
        dept_name VARCHAR(60),
        contents  VARCHAR(200),
        remark    VARCHAR(200),
        PRIMARY KEY (emp_seq, int_seq, ord_seq),
        INDEX ix_date (ord_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS hr_dept_moves (
        emp_seq   INT NOT NULL,
        beg_date  CHAR(8) NOT NULL,
        end_date  CHAR(8),
        dept_name VARCHAR(60),
        PRIMARY KEY (emp_seq, beg_date),
        INDEX ix_beg (beg_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS hr_rests (
        emp_seq  INT NOT NULL,
        beg_date CHAR(8) NOT NULL,
        end_date CHAR(8),
        is_ret   TINYINT DEFAULT 0,
        remark   VARCHAR(200),
        PRIMARY KEY (emp_seq, beg_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS hr_certificates (
        emp_seq    INT NOT NULL,
        certi_seq  INT NOT NULL,
        issue_date CHAR(8),
        issue_no   VARCHAR(30),
        usage_txt  VARCHAR(100),
        submit_to  VARCHAR(100),
        PRIMARY KEY (emp_seq, certi_seq),
        INDEX ix_issue (issue_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


def s(v):
    return (str(v).strip() if v is not None else "")


def fetch_erp():
    c = erp_conn()
    cur = c.cursor()

    cur.execute("""
        SELECT e.EmpSeq, e.Empid, e.EmpName, e.DeptSeq, d.DeptName,
               e.EntDate, e.RetireDate, i.BirthDate, i.SMSexSeq, u.ValText
          FROM _TDAEmp e
          LEFT JOIN _TDADept  d ON d.DeptSeq = e.DeptSeq
          LEFT JOIN _TDAEmpIn i ON i.EmpSeq  = e.EmpSeq
          LEFT JOIN _TDAEmpUserDefine u ON u.EmpSeq = e.EmpSeq AND u.Serl = %d
    """ % CAR_SERL)
    # 사번이 없는 사람은 우리 직원이 아니다 (ERP 에 등록만 되어 있는 외부 인력).
    # 급여명세서로 확인 — 2026-07 급여 받은 160명은 전원 사번이 있고,
    # 사번 없는 사람은 급여도 없고 명부관리에도 없다 (2026-08-02 사용자 결정).
    emps = [(int(r[0]), s(r[1]), s(r[2]), int(r[3] or 0), s(r[4]),
             s(r[5]), s(r[6]), s(r[7]), SEX.get(s(r[8]), ""),
             (" ".join(s(r[9]).split()) if CAR_RE.match(s(r[9])) else ""))
            for r in cur.fetchall() if s(r[1])]

    cur.execute("""
        SELECT o.EmpSeq, o.IntSeq, o.OrdSeq, o.OrdDate, d.DeptName, o.Contents, o.Remark
          FROM _THRAdmOrdEmp o LEFT JOIN _TDADept d ON d.DeptSeq = o.DeptSeq
    """)
    orders = [(int(r[0]), int(r[1] or 0), int(r[2] or 0), s(r[3]),
               s(r[4]), s(r[5])[:200], s(r[6])[:200])
              for r in cur.fetchall() if s(r[3])]

    cur.execute("""
        SELECT m.EmpSeq, m.DeptBegDate, m.DeptEndDate, d.DeptName
          FROM _THRAdmMoveDeptHist m LEFT JOIN _TDADept d ON d.DeptSeq = m.DeptSeq
    """)
    moves = [(int(r[0]), s(r[1]), s(r[2]), s(r[3])) for r in cur.fetchall() if s(r[1])]

    cur.execute("""SELECT EmpSeq, RestBegDate, RestEndDate, IsRet, Remark
                     FROM _THRAdmEmpRest""")
    rests = [(int(r[0]), s(r[1]), s(r[2]), 1 if s(r[3]) == "1" else 0, s(r[4])[:200])
             for r in cur.fetchall() if s(r[1])]

    cur.execute("""SELECT EmpSeq, CertiSeq, IssueDate, IssueNo, CertiUseage, CertiSubmit
                     FROM _THRBasCertificate""")
    certs = [(int(r[0]), int(r[1] or 0), s(r[2]), s(r[3])[:30],
              s(r[4])[:100], s(r[5])[:100]) for r in cur.fetchall()]

    c.close()
    return emps, orders, moves, rests, certs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 반영한다 (기본은 검증 모드)")
    args = ap.parse_args()

    emps, orders, moves, rests, certs = fetch_erp()
    conn = att_conn()
    cur = conn.cursor()
    for ddl in DDL:
        cur.execute(ddl)
    conn.commit()

    # tuser 매핑 — 사번(idno)이 같은 사람. 겹치면 매핑하지 않는다.
    cur.execute("SELECT id, idno FROM tuser WHERE idno IS NOT NULL AND idno <> ''")
    by_idno = {}
    for tid, idno in cur.fetchall():
        by_idno.setdefault(str(idno).strip(), []).append(tid)
    uid_of = {k: v[0] for k, v in by_idno.items() if len(v) == 1}

    active = [e for e in emps if e[6] == ACTIVE]
    mapped = [e for e in active if e[1] in uid_of]
    with_birth = [e for e in active if e[7]]
    today = datetime.date.today()

    cur.execute("SELECT COUNT(*) FROM hr_employees")
    before = cur.fetchone()[0]

    print("=" * 78)
    print("■ ERP 인사자료 동기화 %s" % ("" if args.apply else "(검증 모드 — 쓰지 않음)"))
    print("  사원 %d명 (재직 %d / 퇴직 %d) — tuser 매핑 %d명 / 생년월일 있는 재직자 %d명"
          % (len(emps), len(active), len(emps) - len(active), len(mapped), len(with_birth)))
    print("  발령 %d건 / 부서이동 %d건 / 휴직 %d건 / 증명서 %d건"
          % (len(orders), len(moves), len(rests), len(certs)))
    print("  hr_employees 현재 %d명 → %d명" % (before, len(emps)))

    this_year = str(today.year)
    print("\n  · 올해 입사 %d명 / 올해 퇴사 %d명"
          % (len([e for e in emps if e[5][:4] == this_year]),
             len([e for e in emps if e[6] != ACTIVE and e[6][:4] == this_year])))
    rest_now = [r for r in rests if r[1] <= today.strftime("%Y%m%d") <= (r[2] or "99991231")]
    print("  · 휴직 중 %d명" % len(rest_now))
    print("  · 차량번호 있는 재직자 %d명 (형태가 아닌 값은 버린다)"
          % len([e for e in active if e[9]]))

    if not args.apply:
        print("\n  검증 모드입니다. 반영하려면 --apply 를 붙여 다시 실행하세요.")
        conn.close()
        return

    # ERP 가 원본이므로 통째로 갈아끼운다 (자료가 작아 증분이 필요 없다)
    cur.execute("DELETE FROM hr_employees")
    cur.executemany(
        """INSERT INTO hr_employees
           (emp_seq, empid, name, dept_seq, dept_name, ent_date, retire_date,
            birth_date, sex, car_no, is_active, t_uid)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        [(e[0], e[1], e[2], e[3], e[4], e[5], e[6], e[7], e[8], e[9],
          1 if e[6] == ACTIVE else 0, uid_of.get(e[1])) for e in emps])

    for tbl, rows, sql in (
        ("hr_orders", orders,
         "INSERT INTO hr_orders (emp_seq,int_seq,ord_seq,ord_date,dept_name,contents,remark) "
         "VALUES (%s,%s,%s,%s,%s,%s,%s)"),
        ("hr_dept_moves", moves,
         "INSERT INTO hr_dept_moves (emp_seq,beg_date,end_date,dept_name) VALUES (%s,%s,%s,%s)"),
        ("hr_rests", rests,
         "INSERT INTO hr_rests (emp_seq,beg_date,end_date,is_ret,remark) VALUES (%s,%s,%s,%s,%s)"),
        ("hr_certificates", certs,
         "INSERT INTO hr_certificates (emp_seq,certi_seq,issue_date,issue_no,usage_txt,submit_to) "
         "VALUES (%s,%s,%s,%s,%s,%s)"),
    ):
        cur.execute("DELETE FROM %s" % tbl)
        if rows:
            cur.executemany(sql, rows)
    conn.commit()

    print("\n  반영 완료")
    for tbl in ("hr_employees", "hr_orders", "hr_dept_moves", "hr_rests", "hr_certificates"):
        cur.execute("SELECT COUNT(*) FROM %s" % tbl)
        print("    %-18s %6d행" % (tbl, cur.fetchone()[0]))
    conn.close()


if __name__ == "__main__":
    main()
