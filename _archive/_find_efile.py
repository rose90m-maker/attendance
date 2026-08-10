#!/usr/bin/env python3
"""_find_efile.py — 발급본 없이 정답지를 얻는 두 갈래 조사 (읽기 전용)

남은 사각지대는 '양변이 같이 틀리는' 오류다. 항등식 검사로는 안 잡히고
발급본과의 대조만 잡는데, 발급본을 뽑지 않고 같은 효과를 내는 길이 둘 있다.

① 국세청 전자신고 데이터
   회사는 2025 귀속 근로소득 지급명세서를 이미 국세청에 전자신고했다.
   그 데이터가 곧 영수증의 전 칸이다 — 법적으로 제출된 정답지가 ERP 안에
   있을 가능성이 높다. 신고 파일(고정폭 텍스트/XML)이나 신고용 테이블을 찾는다.

② 암호화된 리포트 프로시저
   본문은 암호화라 못 읽지만 실행은 된다. 이름과 파라미터는 sys.objects /
   sys.parameters 에 보인다. 후보와 파라미터를 나열한다 (실행은 하지 않는다 —
   실행은 BEGIN TRAN…ROLLBACK 으로 감싼 별도 단계에서).

ERP 는 읽기만 한다.

사용:  python3 _archive/_find_efile.py [--yy 2025]
"""
import argparse
import os
import sys

HERE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import wht_receipt as W

ap = argparse.ArgumentParser()
ap.add_argument("--yy", default="2025")
args = ap.parse_args()


def head(t):
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


conn = W._conn()
cur = conn.cursor()

# ── ①-1 신고 파일/데이터로 보이는 테이블 ────────────────────
head("①-1 전자신고 관련 이름의 테이블 (행수 포함)")
cur.execute("""SELECT t.TABLE_NAME FROM INFORMATION_SCHEMA.TABLES t
               WHERE t.TABLE_TYPE='BASE TABLE'
                 AND (t.TABLE_NAME LIKE '%WPR%' OR t.TABLE_NAME LIKE '%PR%')
                 AND (t.TABLE_NAME LIKE '%File%' OR t.TABLE_NAME LIKE '%Xml%'
                      OR t.TABLE_NAME LIKE '%Efile%' OR t.TABLE_NAME LIKE '%Edoc%'
                      OR t.TABLE_NAME LIKE '%Submit%' OR t.TABLE_NAME LIKE '%Decl%'
                      OR t.TABLE_NAME LIKE '%Report%' OR t.TABLE_NAME LIKE '%Hometax%'
                      OR t.TABLE_NAME LIKE '%전자%' OR t.TABLE_NAME LIKE '%신고%')
               ORDER BY t.TABLE_NAME""")
tbls = [r[0] for r in cur.fetchall()]
if not tbls:
    print("  (이름으로는 없음)")
for t in tbls:
    try:
        cur.execute(f"SELECT COUNT(*) FROM [{t}]")
        n = cur.fetchone()[0]
        print(f"  {t:<52}{n:>10,}행")
    except Exception as e:
        print(f"  {t:<52}조회 실패 {str(e)[:40]}")

# ── ①-2 큰 텍스트 컬럼에서 신고 레코드 흔적 찾기 ────────────
head("①-2 큰 텍스트(nvarchar max급) 컬럼 — 신고 파일이 통째로 저장된 곳 후보")
cur.execute("""SELECT c.TABLE_NAME, c.COLUMN_NAME, c.CHARACTER_MAXIMUM_LENGTH
               FROM INFORMATION_SCHEMA.COLUMNS c
               JOIN INFORMATION_SCHEMA.TABLES t
                 ON t.TABLE_NAME=c.TABLE_NAME AND t.TABLE_TYPE='BASE TABLE'
               WHERE c.DATA_TYPE IN ('nvarchar','varchar','ntext','text')
                 AND (c.CHARACTER_MAXIMUM_LENGTH=-1
                      OR c.CHARACTER_MAXIMUM_LENGTH>=2000)
                 AND c.TABLE_NAME LIKE '%PR%'
               ORDER BY c.TABLE_NAME""")
cands = cur.fetchall()
print(f"  후보 {len(cands)}개 — 내용 표본 검사")
found = 0
for t, c, ln in cands:
    try:
        cur.execute(f"SELECT TOP 1 LEFT([{c}], 300) FROM [{t}] "
                    f"WHERE [{c}] IS NOT NULL AND LEN([{c}]) > 200")
        r = cur.fetchone()
    except Exception:
        continue
    if not r or not r[0]:
        continue
    s = str(r[0])
    # 지급명세서 전자신고 파일의 흔적: 레코드 구분자/한글 키워드/주민번호 패턴
    if any(k in s for k in ("지급명세서", "원천징수", "소득자별", "C소득자")) \
       or (s[:2].isalpha() and s.count(" ") > 30):
        found += 1
        print(f"\n  ▸ {t}.{c}")
        print(f"    표본: {s[:160]!r}")
if not found:
    print("  (신고 파일 흔적을 못 찾음 — 신고를 ERP 밖 프로그램으로 했을 수 있음)")

# ── ①-3 국세청(Nts) 계열 중 아직 안 쓴 테이블 ───────────────
head("①-3 Nts 계열 테이블 전체 — 2025 행수 (우리가 아직 안 읽는 것 포함)")
cur.execute("""SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
               WHERE TABLE_TYPE='BASE TABLE' AND TABLE_NAME LIKE '%Nts%'
               ORDER BY TABLE_NAME""")
for (t,) in cur.fetchall():
    try:
        cur.execute(f"""SELECT COUNT(*) FROM [{t}] WHERE YY=%s""", (args.yy,))
        n = cur.fetchone()[0]
    except Exception:
        try:
            cur.execute(f"SELECT COUNT(*) FROM [{t}]")
            n = cur.fetchone()[0]
        except Exception:
            continue
    if n:
        print(f"  {t:<52}{n:>10,}행")

# ── ② 암호화 프로시저 후보와 파라미터 ───────────────────────
head("② 실행 가능한 리포트 프로시저 후보 (암호화 포함)")
cur.execute("""SELECT o.name,
                      OBJECTPROPERTY(o.object_id,'IsEncrypted') AS enc
               FROM sys.objects o
               WHERE o.type='P'
                 AND (o.name LIKE '%AbrIncome%' OR o.name LIKE '%AdjTot%')
               ORDER BY o.name""")
procs = cur.fetchall()
if not procs:
    print("  (없음)")
for nm, enc in procs:
    cur.execute("""SELECT p.name, TYPE_NAME(p.user_type_id)
                   FROM sys.parameters p
                   JOIN sys.objects o ON o.object_id=p.object_id
                   WHERE o.name=%s ORDER BY p.parameter_id""", (nm,))
    params = ", ".join(f"{a} {b}" for a, b in cur.fetchall()) or "(없음)"
    print(f"\n  ▸ {nm}  {'🔒 암호화' if enc else '평문'}")
    print(f"    파라미터: {params}")

conn.close()
print("""
다음 단계
  ①에서 신고 데이터가 나오면 그걸 정답지로 전수 대조기를 만든다 (가장 강함).
  ②에서 이름이 Rpt…AbrIncome… 인 프로시저가 보이면, 파라미터를 보고
  BEGIN TRAN → EXEC → 결과 확인 → ROLLBACK 으로 한 명 실행해 본다.
  암호화돼 있어도 실행은 된다. 롤백으로 감싸므로 로그를 쓰더라도 되돌아간다.""")
