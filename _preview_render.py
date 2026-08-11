#!/usr/bin/env python3
"""_preview_render.py — 실데이터로 대시보드 미리보기 HTML 을 만든다.

운영 dashboard() 를 그대로 호출해 컨텍스트를 받고, 새 디자인 템플릿에 꽂아
정적 HTML 한 장으로 떨군다. 서버를 띄우지 않으므로 MQTT·모니터 스레드가
살아 있을 일이 없다 (_preview_dump 가 import 전에 차단한다).

달력·MES 는 운영에서 JS 가 별도 API 로 불러오는 영역이라 컨텍스트에 없다.
미리보기에서도 실데이터로 보여야 하므로 여기서 같은 출처를 직접 조회한다.

사용:  .venv/bin/python3 _preview_render.py [--open]
"""
import calendar as _cal
import os
import sys
import webbrowser
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from _preview_dump import capture  # noqa: E402  (부작용 차단이 여기서 먼저 걸린다)
import app_maria as A  # noqa: E402

OUT = os.path.join(HERE, "_preview_live.html")

# 일정 색 → 의미. 브리프의 색 규칙(Primary/Success/Warning/Danger)에 맞춘다.
EVENT_KIND = {
    "연차": "leave", "휴가": "leave", "반차": "leave",
    "교육": "edu", "행사": "event", "회의": "event",
    "점검": "check", "휴무": "holiday",
}


def sparkline(vals, w=240, h=64, pad=4):
    """시간대별 값 → SVG polyline 좌표. 아직 안 지난 시간(뒤쪽 0)은 자른다."""
    last = 0
    for i, v in enumerate(vals):
        if v:
            last = i
    series = vals[:last + 1] or [0]
    mx = max(series) or 1
    n = len(series)
    step = w / max(n - 1, 1)
    pts = []
    for i, v in enumerate(series):
        x = round(i * step, 1)
        y = round(h - pad - (v / mx) * (h - pad * 2), 1)
        pts.append(f"{x},{y}")
    line = " ".join(pts)
    area = f"{line} {w},{h} 0,{h}"
    return line, area, (pts[-1] if pts else "0,0")


def month_calendar(today):
    """이번 달 달력 격자 + 일정. 운영 /api/schedule_events 와 같은 테이블을 본다."""
    conn = A._conn()
    cur = conn.cursor()
    ym = f"{today.year}-{today.month:02d}"
    ev_by_day = {}
    try:
        cur.execute("""SELECT title, event_date, color FROM schedule_events
                       WHERE event_date LIKE %s ORDER BY event_date""", (ym + "%",))
        for title, edate, color in cur.fetchall():
            try:
                d = int(str(edate)[8:10])
            except (ValueError, IndexError):
                continue
            kind = "event"
            for word, k in EVENT_KIND.items():
                if word in (title or ""):
                    kind = k
                    break
            ev_by_day.setdefault(d, []).append({"title": title, "kind": kind})
    except Exception as e:
        print(f"  (일정 조회 건너뜀: {e})")
    conn.close()

    try:
        import holidays
        kr = holidays.KR(years=today.year)
    except Exception:
        kr = {}

    weeks = []
    for wk in _cal.Calendar(firstweekday=6).monthdatescalendar(today.year, today.month):
        row = []
        for d in wk:
            row.append({
                "day": d.day,
                "in_month": d.month == today.month,
                "is_today": d == today,
                "dow": d.weekday(),                      # 0=월 … 6=일
                "holiday": kr.get(d) if d.month == today.month else None,
                "events": ev_by_day.get(d.day, []) if d.month == today.month else [],
            })
        weeks.append(row)
    return weeks


def mes_latest():
    """MES 온습도 최신값 — 운영에서 /mes/api/env 가 보는 테이블."""
    rows = []
    try:
        conn = A._conn()
        cur = conn.cursor()
        cur.execute("""SELECT e.device_id, e.temperature, e.humidity, e.recorded_at
                       FROM mes_env_log e
                       JOIN (SELECT device_id, MAX(recorded_at) m
                             FROM mes_env_log GROUP BY device_id) x
                         ON x.device_id = e.device_id AND x.m = e.recorded_at
                       ORDER BY e.device_id LIMIT 6""")
        for dev, t, h, at in cur.fetchall():
            rows.append({"device": dev, "temp": float(t or 0),
                         "humi": float(h or 0), "at": str(at)[11:16]})
        conn.close()
    except Exception as e:
        print(f"  (MES 조회 건너뜀: {e})")
    return rows


def main():
    ctx = capture()
    today = ctx.get("today") or date.today()

    line, area, endpt = sparkline(ctx.get("power_info", {}).get("hourly_kwh", []))
    ctx["spark_line"], ctx["spark_area"], ctx["spark_end"] = line, area, endpt
    ctx["cal_weeks"] = month_calendar(today)
    ctx["mes_rows"] = mes_latest()

    with A.app.app_context():
        html = A.app.jinja_env.get_template("_preview_dashboard.html").render(**ctx)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)

    p = ctx.get("power_info", {})
    n_ev = sum(len(d["events"]) for w in ctx["cal_weeks"] for d in w)
    print(f"생성: {OUT}  ({len(html):,}자)")
    print(f"  출근 {ctx['att_summary']['checked_in']}명 · "
          f"근무예외 {len(ctx['leave_today']) + len(ctx['etc_today'])}명 · "
          f"공지 {len(ctx['notices'])}건 · 생일 {len(ctx['birthdays'])}명")
    print(f"  달력 일정 {n_ev}건 · MES 장치 {len(ctx['mes_rows'])}대")
    print(f"  전력 오늘 {p.get('today_kwh')} kWh · 당월 {p.get('rt_kwh'):,.0f} kWh · "
          f"청구 {p.get('rt_bill'):,}원")
    if "--open" in sys.argv:
        webbrowser.open("file://" + OUT)


if __name__ == "__main__":
    main()
