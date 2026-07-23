import os, sys
from dotenv import load_dotenv
load_dotenv('/app/.env')
import pymysql, pymssql
RUN = '--run' in sys.argv

# ERP 카드 마스터
ec = pymssql.connect(server=os.environ['ERP_DB_HOST'], port=int(os.environ['ERP_DB_PORT']),
                     user=os.environ['ERP_DB_USER'], password=os.environ['ERP_DB_PASSWORD'],
                     database=os.environ['ERP_DB_NAME'], charset='UTF-8')
ecur=ec.cursor()
ecur.execute("""SELECT e.Empid, e.EmpName, c.CardId FROM _TXPRWkEmpCard c
                JOIN _TDAEmp e ON c.EmpSeq=e.EmpSeq
                WHERE c.CardId IS NOT NULL AND c.CardId<>''""")
erp_card={(r[0] or '').strip():((r[1] or '').strip(),(r[2] or '').strip()) for r in ecur.fetchall()}
ec.close()
print(f"ERP 카드 등록: {len(erp_card)}명\n")

ac = pymysql.connect(host=os.environ.get('DB_HOST','192.168.100.11'), port=int(os.environ.get('DB_PORT',3307)),
                     user=os.environ.get('DB_USER','root'), password=os.environ['DB_PASSWORD'],
                     database='attendance', charset='utf8mb4')
cur = ac.cursor()
cur.execute("""SELECT u.id,u.name,u.idno FROM tuser u
               WHERE (u.cardnum IS NULL OR u.cardnum='')
                 AND u.idno IS NOT NULL AND u.idno<>''
                 AND (u.retire_date IS NULL OR u.retire_date='')""")
targets=cur.fetchall()
print(f"=== 카드번호 비어있는 tuser {len(targets)}명 ===")
fill=[]
for uid,nm,idno in targets:
    e=erp_card.get(idno)
    # 실제 출입기록에서 쓰는 카드도 확인
    cur.execute("SELECT e_card,COUNT(*) FROM tenter WHERE e_id=%s AND e_card<>'' GROUP BY e_card ORDER BY COUNT(*) DESC LIMIT 1",(uid,))
    used=cur.fetchone()
    src = e[1] if e else (used[0] if used else None)
    tag = 'ERP' if e else ('출입기록' if used else '없음')
    print(f"   {nm:8} 사번={idno} → 카드={src or '(없음)'} [{tag}]")
    if src: fill.append((uid,nm,src,tag))

print(f"\n채울 수 있는 대상: {len(fill)}명")
if RUN and fill:
    for uid,nm,card,tag in fill:
        cur.execute("UPDATE tuser SET cardnum=%s WHERE id=%s",(card,uid))
    ac.commit()
    print("✅ 카드번호 반영 완료")
    for uid,nm,card,tag in fill: print(f"   {nm}: {card} ({tag})")
elif not RUN:
    print("[미리보기] --run 으로 실행")
ac.close()
