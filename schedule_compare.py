#!/usr/bin/env python3
"""
근무표(엑셀 원본) ↔ 근무보고서(schedule_record) 매일 대조

원본: smb://192.168.100.3/taein_hq/이민영/▶ 2026 근무표/YYYY_MM 근무표.xlsx
  시트 '전기근태' / '전자근태'
  3행 헤더에서 일자→열 인덱스를 읽고, 인원별 4행(기본/연장/야간/기타)을 파싱한다.
  ※ 이 엑셀은 openpyxl이 'phonetic' 속성 때문에 열지 못해 python-calamine을 사용한다.
  ※ 2025년 양식은 날짜가 '일' 숫자, 2026년은 '1(월)' 형태 → 둘 다 대응.

시스템: attendance.schedule_record (source_type '수정근무보고서' 우선, 없으면 '보고서')

결과를 schedule_diff 테이블에 저장하고, **이전 실행에 없던 신규 차이만**
태인 알림방(텔레그램)으로 통보한다.

Mac에서 실행 (파일서버 SMB 마운트 + calamine 이 Mac에 있음).
매일 08:00 LaunchAgent(com.taein.schedule-compare)로 자동 실행.

사용:
  python3 schedule_compare.py                 # 당월, 알림 발송
  python3 schedule_compare.py --month 202607  # 특정 월
  python3 schedule_compare.py --dry-run       # 저장·발송 없이 결과만 출력
"""
import argparse
import os
import re
import ssl
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# 근무표는 연도별 폴더로 분리되어 있다: ".../이민영/▶ 2026 근무표/2026_07 근무표.xlsx"
SCHEDULE_ROOT = os.environ.get("SCHEDULE_ROOT", "/Volumes/taein_hq/이민영")
SCHEDULE_DIR_FMT = os.environ.get("SCHEDULE_DIR_FMT", "▶ {year} 근무표")
SHEETS = ("전기근태", "전자근태")
KINDS = {"기본": "basic", "연장": "ot", "야간": "night", "기타": "etc"}
HEADER_ROW = 2          # 0-based: 3행
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
MAX_TG_LINES = 15


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def conn_db():
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "192.168.100.11"),
        port=int(os.environ.get("DB_PORT", 3307)),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ["DB_PASSWORD"],
        database="attendance",
        charset="utf8mb4",
    )


def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  ⚠️  텔레그램 설정 없음 — 발송 생략")
        return
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": msg}).encode()
        urlopen(Request(url, data), context=ctx, timeout=10)
        print("  ✅ 텔레그램 발송")
    except Exception as e:
        print(f"  ⚠️  텔레그램 발송 실패: {e}")


def schedule_dir(year):
    """연도별 근무표 폴더 (한글 NFD 파일명 대응해 실제 항목명으로 매칭)"""
    want = nfc(SCHEDULE_DIR_FMT.format(year=year))
    try:
        for n in os.listdir(SCHEDULE_ROOT):
            if nfc(n) == want:
                return os.path.join(SCHEDULE_ROOT, n)
    except OSError:
        return None
    return None


def find_xlsx(ym):
    """YYYYMM → 해당 월 근무표 파일 경로 ('근무대체' 파일은 제외)"""
    y, m = int(ym[:4]), int(ym[4:6])
    d = schedule_dir(y)
    if not d:
        return None
    want = f"{y}_{m:02d} 근무표.xlsx"
    try:
        for n in os.listdir(d):
            if nfc(n) == want:
                return os.path.join(d, n)
    except OSError:
        return None
    return None


def target_months(today, explicit=None):
    """대조 대상 월 목록.
    당월은 항상, 매월 10일까지는 전월도 함께 본다.
    (근무보고서는 월초에 전월분이 소급 작성·수정되므로 전월 추적을 끊으면 안 됨)"""
    if explicit:
        return [explicit]
    cur_ym = today.strftime("%Y%m")
    if today.day > 10:
        return [cur_ym]
    prev = date(today.year, today.month, 1) - timedelta(days=1)
    return [prev.strftime("%Y%m"), cur_ym]


def num(v):
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0          # '연차' 등 문자 → 근무시간 0


def parse_excel(path, year, month):
    """→ {(이름, 일): {basic, ot, night, etc}}, {이름: 부서}"""
    from python_calamine import CalamineWorkbook
    wb = CalamineWorkbook.from_path(path)
    data, dept_of = defaultdict(dict), {}
    for sheet in SHEETS:
        if sheet not in wb.sheet_names:
            continue
        rows = wb.get_sheet_by_name(sheet).to_python(skip_empty_area=False)
        if len(rows) <= HEADER_ROW:
            continue
        # 헤더에서 일자 → 열 인덱스
        daymap = {}
        for i, v in enumerate(rows[HEADER_ROW]):
            try:
                d = int(float(v))
            except (TypeError, ValueError):
                continue
            if 1 <= d <= 31 and d not in daymap:
                daymap[d] = i
        if not daymap:
            continue
        for r in rows[HEADER_ROW + 1:]:
            if len(r) < 4:
                continue
            dept = str(r[1] or "").strip()
            name = str(r[2] or "").strip()
            kind = str(r[3] or "").strip()
            if not name or kind not in KINDS:
                continue
            dept_of.setdefault(name, dept)
            for day, ci in daymap.items():
                if ci >= len(r) or r[ci] in (None, ""):
                    continue
                data[(name, day)][KINDS[kind]] = r[ci]
    return data, dept_of


def load_roster_dept(cur):
    """사원명부의 정식 부서명. 근무표 엑셀의 부서 칸은 뭉뚱그려져 있어
    (전기제조팀 42명이 모두 '생산') 명부 값을 우선 사용한다."""
    cur.execute("""SELECT name, dept FROM employee_roster
                   WHERE dept IS NOT NULL AND dept <> ''""")
    return {r[0]: r[1] for r in cur.fetchall()}


def load_tags(cur, ym):
    """출입기록(tenter) → {(이름, 일): (최초, 최종, 태그수)}"""
    cur.execute("""SELECT e_name, e_date, MIN(e_time), MAX(e_time), COUNT(*)
                   FROM tenter WHERE e_date LIKE %s AND e_id>=0
                   GROUP BY e_name, e_date""", (ym + "%",))
    return {(r[0], int(r[1][6:8])): (r[2], r[3], r[4]) for r in cur.fetchall()}


def _mins(t):
    return int(t[:2]) * 60 + int(t[2:4]) if t and len(t) >= 4 else None


def load_system(cur, ym):
    """→ {(이름, 일): (basic, ot, night, source_type)}"""
    cur.execute("""SELECT emp_name, work_date, basic_h, ot_h, night_h, source_type
                   FROM schedule_record WHERE work_date LIKE %s""", (ym + "%",))
    tmp = defaultdict(dict)
    for nm, wd, b, o, n, st in cur.fetchall():
        tmp[(nm, int(wd[6:8]))][st] = (float(b or 0), float(o or 0), float(n or 0))
    out = {}
    for key, srcs in tmp.items():
        st = "수정근무보고서" if "수정근무보고서" in srcs else next(iter(srcs))
        b, o, n = srcs[st]
        out[key] = (b, o, n, st)
    return out


def ensure_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS `schedule_diff` (
            `id` INT AUTO_INCREMENT PRIMARY KEY,
            `ym` VARCHAR(6) NOT NULL,
            `work_date` DATE NOT NULL,
            `emp_name` VARCHAR(50) NOT NULL,
            `dept` VARCHAR(30) DEFAULT '',
            `diff_type` VARCHAR(20) NOT NULL,
            `xl_basic` DOUBLE DEFAULT 0, `xl_ot` DOUBLE DEFAULT 0, `xl_night` DOUBLE DEFAULT 0,
            `sys_basic` DOUBLE DEFAULT 0, `sys_ot` DOUBLE DEFAULT 0, `sys_night` DOUBLE DEFAULT 0,
            `source_type` VARCHAR(20) DEFAULT '',
            `first_seen` DATETIME DEFAULT CURRENT_TIMESTAMP,
            `last_seen` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            `resolved` TINYINT NOT NULL DEFAULT 0,
            UNIQUE KEY `uq_diff` (`work_date`, `emp_name`, `diff_type`),
            KEY `idx_ym` (`ym`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)


def process_month(cur, ym, args):
    """한 달 대조 → (신규키집합, diffs, 미해소수, 해소수) / 파일 없으면 None"""
    year, month = int(ym[:4]), int(ym[4:6])

    path = find_xlsx(ym)
    if not path:
        # 월초에 당월 파일이 아직 안 만들어졌을 수 있음 — 오류가 아니라 건너뜀
        print(f"  ⏭  {year}-{month:02d}: 근무표 파일 없음 — 건너뜀")
        return None
    print(f"📄 {year}-{month:02d} 근무표: {nfc(os.path.basename(path))}")

    xl, xl_dept = parse_excel(path, year, month)
    sysd = load_system(cur, ym)
    tags = load_tags(cur, ym)
    roster_dept = load_roster_dept(cur)
    # 명부 부서명 우선, 없으면 근무표 엑셀의 부서 칸
    dept_of = {nm: roster_dept.get(nm) or xl_dept.get(nm, "") for nm in xl_dept}
    sys_names = {nm for nm, _ in sysd}
    print(f"   엑셀 {len(dept_of)}명/{len(xl)}셀 · 시스템 {len(sys_names)}명/{len(sysd)}셀")

    # ── 차이 산출 ──
    # 값불일치: 양쪽에 있는데 기본/연장/야간이 다름
    # 미작성  : 엑셀에 근무·휴가 기록이 있는데 시스템에 행이 없음
    #           (엑셀도 0/0/0인 비근무일은 차이로 보지 않음)
    diffs = []
    for (nm, day), xv in xl.items():
        if nm not in sys_names:
            continue                      # 인원 자체가 시스템에 없으면 대조 제외
        xb, xo, xn = num(xv.get("basic")), num(xv.get("ot")), num(xv.get("night"))
        etc = xv.get("etc")
        etc_txt = etc.strip() if isinstance(etc, str) and etc.strip() else ""
        rec = sysd.get((nm, day))
        wd = date(year, month, day)
        if rec is None:
            if xb + xo + xn > 0 or etc_txt:
                diffs.append((wd, nm, dept_of.get(nm, ""), "미작성",
                              xb, xo, xn, 0.0, 0.0, 0.0, ""))
            continue
        sb, so, sn, st = rec
        if abs(xb - sb) > 0.01 or abs(xo - so) > 0.01 or abs(xn - sn) > 0.01:
            diffs.append((wd, nm, dept_of.get(nm, ""), "값불일치",
                          xb, xo, xn, sb, so, sn, st))

    # ── 출입기록 기반 연장근무 검증 (주간조만) ──
    # 2026-07 사례: 7/3 SMT 7명이 정시 퇴근인데 연장 2h 기록, 7/20 PQC·QA 6명은 20시
    # 퇴근인데 연장 0. 둘 다 근무표·출입기록이 일치했고 보고서만 틀렸다.
    for (nm, day), rec in sysd.items():
        tg = tags.get((nm, day))
        if not tg or tg[2] < 2:          # 태그 2회 미만이면 판단 불가
            continue
        sb, so, sn, _st = rec
        if sn > 0 or sb <= 0:            # 야간조·비근무일 제외
            continue
        out = _mins(tg[1])
        if out is None:
            continue
        wd = date(year, month, day)
        xv = xl.get((nm, day))
        xb = num(xv.get("basic")) if xv else 0.0
        xo = num(xv.get("ot")) if xv else 0.0
        xn = num(xv.get("night")) if xv else 0.0
        if so > 0 and out < 18 * 60:
            diffs.append((wd, nm, dept_of.get(nm, ""), "연장과다",
                          xb, xo, xn, sb, so, sn, f"퇴근 {tg[1][:2]}:{tg[1][2:4]}"))
        elif so == 0 and out >= 20 * 60:
            diffs.append((wd, nm, dept_of.get(nm, ""), "연장누락",
                          xb, xo, xn, sb, so, sn, f"퇴근 {tg[1][:2]}:{tg[1][2:4]}"))

    # ── 근무표에 칸이 없는데 시스템에만 있는 날 ──
    # 근무표는 나중에 채워지므로 7일 지난 날짜만 대상 (월말 미기입 오탐 방지)
    grace = date.today() - timedelta(days=7)
    for (nm, day), rec in sysd.items():
        if (nm, day) in xl or nm not in dept_of:
            continue
        wd = date(year, month, day)
        if wd > grace:
            continue
        sb, so, sn, _st = rec
        if sb + so + sn <= 0:
            continue
        diffs.append((wd, nm, dept_of.get(nm, ""), "근무표없음",
                      0.0, 0.0, 0.0, sb, so, sn, ""))

    kinds = Counter(d[3] for d in diffs)
    print(f"   차이 {len(diffs)}건 (" +
          " · ".join(f"{k} {v}" for k, v in kinds.most_common()) + ")")

    if args.dry_run:
        for d in sorted(diffs)[:40]:
            print(f"     {d[0]} {d[1]}[{d[2]}] {d[3]} 엑셀 {d[4]:g}/{d[5]:g}/{d[6]:g}"
                  f" vs 시스템 {d[7]:g}/{d[8]:g}/{d[9]:g}")
        return set(), diffs, len(diffs), 0

    # ── 기존 미해소 차이 (신규 판정 기준) ──
    cur.execute("""SELECT work_date, emp_name, diff_type FROM schedule_diff
                   WHERE ym=%s AND resolved=0""", (ym,))
    known = {(r[0], r[1], r[2]) for r in cur.fetchall()}
    current = {(d[0], d[1], d[3]) for d in diffs}
    new_keys = current - known

    # 저장 (신규 insert / 기존 last_seen 갱신)
    for d in diffs:
        cur.execute("""INSERT INTO schedule_diff
              (ym, work_date, emp_name, dept, diff_type,
               xl_basic, xl_ot, xl_night, sys_basic, sys_ot, sys_night,
               source_type, resolved)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0)
            ON DUPLICATE KEY UPDATE
              dept=VALUES(dept), xl_basic=VALUES(xl_basic), xl_ot=VALUES(xl_ot),
              xl_night=VALUES(xl_night), sys_basic=VALUES(sys_basic),
              sys_ot=VALUES(sys_ot), sys_night=VALUES(sys_night),
              source_type=VALUES(source_type), resolved=0""",
                    (ym, d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7], d[8], d[9], d[10]))
    # 이번에 사라진 차이는 해소 처리
    for k in known - current:
        cur.execute("""UPDATE schedule_diff SET resolved=1
                       WHERE ym=%s AND work_date=%s AND emp_name=%s AND diff_type=%s""",
                    (ym, k[0], k[1], k[2]))
    cur.connection.commit()

    resolved_cnt = len(known - current)
    print(f"   신규 {len(new_keys)}건 · 해소 {resolved_cnt}건 · 기존유지 {len(current & known)}건")
    return new_keys, diffs, len(current), resolved_cnt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="YYYYMM (기본: 당월 + 매월 10일까지는 전월)")
    ap.add_argument("--dry-run", action="store_true", help="저장·발송 없이 결과만 출력")
    ap.add_argument("--baseline", action="store_true",
                    help="현재 차이를 기준선으로 저장하고 알림은 보내지 않음 (최초 1회)")
    args = ap.parse_args()

    today = date.today()
    months = target_months(today, args.month)
    print(f"🗓  대조 대상: {', '.join(months)}")

    conn = conn_db(); cur = conn.cursor()
    ensure_table(cur)

    results = []          # (ym, new_keys, diffs, open_cnt, resolved_cnt)
    for ym in months:
        r = process_month(cur, ym, args)
        if r:
            results.append((ym, *r))
    conn.close()

    if not results:
        print("❌ 대조 가능한 근무표가 없습니다.")
        print(f"   경로 확인: {SCHEDULE_ROOT}/{SCHEDULE_DIR_FMT.format(year=today.year)}")
        print("   파일서버(192.168.100.3) taein_hq 공유 마운트도 확인하세요.")
        return 1

    if args.dry_run:
        return 0
    if args.baseline:
        tot = sum(r[3] for r in results)
        print(f"  ℹ️  기준선 저장 완료 (총 {tot}건) — 알림 생략."
              f" 다음 실행부터 신규 차이만 통보합니다.")
        return 0

    # ── 신규 차이만 알림 (여러 달을 한 통에 정리) ──
    total_new = sum(len(r[1]) for r in results)
    if not total_new:
        print("  ℹ️  신규 차이 없음 — 알림 생략")
        return 0

    lines = [f"📋 근무표 대조 — 신규 차이 {total_new}건", ""]
    shown = 0
    for ym, new_keys, diffs, open_cnt, resolved_cnt in results:
        if not new_keys:
            continue
        by = {(d[0], d[1], d[3]): d for d in diffs}
        lines.append(f"📅 {int(ym[:4])}년 {int(ym[4:6])}월 ({len(new_keys)}건)")
        for k in sorted(new_keys):
            if shown >= MAX_TG_LINES:
                break
            d = by[k]
            if d[3] == "미작성":
                lines.append(f"· {d[0]} {d[1]} [{d[2]}] 보고서 미작성 "
                             f"(근무표 {d[4]:g}/{d[5]:g}/{d[6]:g})")
            elif d[3] == "근무표없음":
                lines.append(f"· {d[0]} {d[1]} [{d[2]}] 근무표 미기입 "
                             f"(보고서 {d[7]:g}/{d[8]:g}/{d[9]:g})")
            elif d[3] in ("연장과다", "연장누락"):
                mark = "연장 과다" if d[3] == "연장과다" else "연장 누락"
                lines.append(f"· {d[0]} {d[1]} [{d[2]}] ⚠️ {mark} — "
                             f"보고서 연장 {d[8]:g}h / {d[10]} "
                             f"(근무표 {d[4]:g}/{d[5]:g}/{d[6]:g})")
            else:
                lines.append(f"· {d[0]} {d[1]} [{d[2]}] 근무표 {d[4]:g}/{d[5]:g}/{d[6]:g}"
                             f" ↔ 보고서 {d[7]:g}/{d[8]:g}/{d[9]:g}")
            shown += 1
        lines.append("")
    if total_new > shown:
        lines.append(f"... 외 {total_new - shown}건")
    summary = " · ".join(
        f"{int(ym[4:6])}월 미해소 {open_cnt}" + (f"/해소 {rc}" if rc else "")
        for ym, _nk, _df, open_cnt, rc in results)
    lines += ["(기본/연장/야간 시간)", summary,
              "👉 https://app.taein.biz/schedule_record"]
    send_telegram("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
