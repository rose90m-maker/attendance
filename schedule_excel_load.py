#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""근무표 엑셀 → 근무표기록관리 적재 (관리자가 보고서를 쓰지 않는 인원 전용)

근무보고서를 통해 근무표기록관리가 채워지지 않는 인원을, 정본인 근무표
엑셀(이민영 작성)에서 직접 읽어 `schedule_record` 에 `원본` 으로 적재한다.

대상 (TARGETS)
    정민재(전기생기)  — 상시
    임동훈(전기품질)  — 상시

원칙
  1. 이들의 근무표기록은 오직 근무표 엑셀에서만 온다. 장용국이 임동훈 근무보고서를
     쓰지만 그건 작성 연습이라 실데이터에 반영하지 않는다 (2026-08-06 결정).
     그래서 `보고서`/`수정근무보고서` 행이 있으면 지운다.
     앱 쪽 차단은 app_maria.py `WR_NO_APPLY_NAMES` 가 담당한다.
  2. 당일·미래는 적재하지 않는다.
  3. 엑셀에 근무도 기타표기도 없는 날(전부 0)은 근무 없음으로 보고 넘어간다.
  4. 엑셀이 정본이므로 값이 바뀌면 덮어쓴다.

실행
    python schedule_excel_load.py                 # 당월 (10일까지는 전월도)
    python schedule_excel_load.py --ym 202607     # 특정 월
    python schedule_excel_load.py --dry-run
"""
import argparse
import os
import ssl
import sys
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen, Request

import pymysql

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# 엑셀 파싱은 대조 배치와 같은 파서를 쓴다 (시트·헤더·NFC 처리가 이미 맞춰져 있다)
from schedule_compare import find_xlsx, parse_excel, num, nfc, target_months

# 적재 대상 — to 를 넣으면 그 달까지만 적재하고 이후는 근무보고서 경로로 넘긴다
TARGETS = {
    "정민재": {"sheet": "전기생기", "to": None},
    "임동훈": {"sheet": "전기품질", "to": None},
}

SOURCE_TYPE = "원본"
LOADER = "근무표"          # uploaded_by 에 남는 이름

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def conn_db():
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "192.168.100.11"),
        port=int(os.environ.get("DB_PORT", 3307)),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ["DB_PASSWORD"],
        database="attendance",
        charset="utf8mb4",
        autocommit=False,
    )


def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": msg}).encode()
        urlopen(Request(url, data=data), timeout=10, context=ctx).read()
    except Exception as e:
        print(f"  ⚠️  텔레그램 발송 실패: {e}")


def active_targets(ym):
    """해당 월에 엑셀 적재 대상인 사람만"""
    out = {}
    for nm, cfg in TARGETS.items():
        if cfg["to"] and ym > cfg["to"]:
            continue
        out[nm] = cfg
    return out


def cell_values(cells):
    """엑셀 한 칸 → (basic, ot, night, etc)

    etc 가 '무급'·'연차' 같은 문자면 그대로 두고, 숫자 0 은 비운다.
    (근무표는 반차를 '기본 4' 로 적기도 해 basic 을 그대로 받는다)
    """
    basic = num(cells.get("basic"))
    ot = num(cells.get("ot"))
    night = num(cells.get("night"))
    raw_etc = cells.get("etc")
    if raw_etc in (None, "", 0, 0.0):
        etc = None
    elif isinstance(raw_etc, str):
        etc = nfc(raw_etc.strip()) or None
    else:
        n = num(raw_etc)
        etc = (f"{n:g}" if n else None)
    return basic, int(ot), int(night), etc


def purge_report_rows(cur, names, ym):
    """대상자의 근무보고서 결재분을 지운다.

    이들의 근무보고서는 작성 연습용이라 실데이터에 반영하지 않기로 했다
    (2026-08-06). 근무표기록은 오직 근무표 엑셀에서만 온다.
    앱 쪽에서도 `WR_NO_APPLY_NAMES` 로 반영을 막아 두었지만, 그 이전에
    적재된 것과 배포 전 결재분이 남을 수 있어 배치에서도 정리한다.
    """
    if not names:
        return 0
    ph = ",".join(["%s"] * len(names))
    cur.execute(f"""
        DELETE FROM schedule_record
        WHERE emp_name IN ({ph}) AND work_date LIKE %s
          AND source_type IN ('보고서', '수정근무보고서')
    """, list(names) + [ym + "%"])
    return cur.rowcount


def process_month(cur, ym, dry_run, today_s):
    targets = active_targets(ym)
    if not targets:
        print(f"  {ym}: 대상 없음 (모두 근무보고서 경로로 전환됨)")
        return []

    path = find_xlsx(ym)
    if not path:
        print(f"  ⚠️  {ym} 근무표 파일 없음 — 건너뜀 (SMB 마운트 확인)")
        return []
    print(f"  {ym} 근무표: {os.path.basename(path)}  대상 {', '.join(targets)}")

    data, _ = parse_excel(path, int(ym[:4]), int(ym[4:6]))
    n_purged = purge_report_rows(cur, list(targets), ym)
    if n_purged:
        print(f"    보고서 결재분 {n_purged}건 제거 (연습용 — 실데이터 반영 안 함)")
    changed = []

    for nm, cfg in targets.items():
        days = sorted(d for (n, d) in data if n == nm)
        if not days:
            print(f"    ⚠️  {nm}: 엑셀에 행이 없음")
            continue
        n_load = n_skip_future = n_skip_empty = 0
        for day in days:
            ymd = f"{ym}{day:02d}"
            if ymd >= today_s:
                n_skip_future += 1
                continue
            basic, ot, night, etc = cell_values(data[(nm, day)])
            if not basic and not ot and not night and not etc:
                n_skip_empty += 1
                continue

            cur.execute("""
                SELECT basic_h, ot_h, night_h, etc FROM schedule_record
                WHERE emp_name=%s AND work_date=%s AND source_type=%s AND sheet_name=%s
            """, (nm, ymd, SOURCE_TYPE, cfg["sheet"]))
            old = cur.fetchone()
            new = (float(basic), int(ot), int(night), etc)
            if old and (float(old[0]), int(old[1]), int(old[2]), old[3]) == new:
                continue

            cur.execute("""
                INSERT INTO schedule_record (emp_name, work_date, basic_h, ot_h, night_h,
                                             etc, source_type, sheet_name, uploaded_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE basic_h=VALUES(basic_h), ot_h=VALUES(ot_h),
                                        night_h=VALUES(night_h), etc=VALUES(etc),
                                        uploaded_at=NOW(), uploaded_by=VALUES(uploaded_by)
            """, (nm, ymd, basic, ot, night, etc, SOURCE_TYPE, cfg["sheet"], LOADER))
            n_load += 1
            label = etc if etc else f"기본{basic:g}" + (f"/연장{ot}" if ot else "") + (f"/야간{night}" if night else "")
            changed.append((nm, ymd, label, "수정" if old else "신규"))

        print(f"    {nm}: 적재 {n_load}건 · 미래 {n_skip_future} · "
              f"근무없음 {n_skip_empty}")
    return changed


def main():
    ap = argparse.ArgumentParser(description="근무표 엑셀 → 근무표기록관리 적재")
    ap.add_argument("--ym", help="특정 월만 (YYYYMM)")
    ap.add_argument("--dry-run", action="store_true", help="미리보기 — 마지막에 롤백")
    args = ap.parse_args()

    today = date.today()
    today_s = today.strftime("%Y%m%d")
    months = target_months(today, args.ym)
    print(f"근무표 엑셀 적재  대상월: {', '.join(months)}"
          f"{'  [DRY-RUN]' if args.dry_run else ''}")

    conn = conn_db()
    cur = conn.cursor()
    changed = []
    try:
        for ym in months:
            changed += process_month(cur, ym, args.dry_run, today_s)

        if args.dry_run:
            conn.rollback()
            print(f"\n[DRY-RUN] 적재예정 {len(changed)}건 — DB 변경 없음")
        else:
            conn.commit()
            print(f"\n완료: 적재 {len(changed)}건")
        for nm, ymd, label, kind in changed[:40]:
            print(f"  · {nm} {ymd[4:6]}/{ymd[6:8]} {label} ({kind})")
        if len(changed) > 40:
            print(f"  … 외 {len(changed) - 40}건")

        if changed and not args.dry_run:
            # 값이 바뀐 건(수정)만 알린다 — 신규 적재는 매일 나오므로 시끄럽다
            fixed = [c for c in changed if c[3] == "수정"]
            if fixed:
                lines = "\n".join(f"· {nm} {ymd[4:6]}/{ymd[6:8]} → {label}"
                                  for nm, ymd, label, _ in fixed[:20])
                send_telegram(f"[근무표 엑셀 적재] 값 변경 {len(fixed)}건\n{lines}")
    except Exception as e:
        conn.rollback()
        print(f"\n❌ 실패 — 롤백: {e}")
        send_telegram(f"[근무표 엑셀 적재] 실패: {e}")
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
