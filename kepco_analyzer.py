"""
KEPCO 전력 분석 함수 모듈
app_maria.py 라우트에서 import하여 사용
"""
import os
import random
from datetime import datetime, timedelta


def _ensure_power_tables(cur):
    """전력관리 테이블이 없으면 자동 생성"""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS power_usage_raw (
            id          BIGINT AUTO_INCREMENT PRIMARY KEY,
            cust_no     VARCHAR(20)   NOT NULL,
            measured_at DATETIME      NOT NULL,
            kwh         DECIMAL(10,3) NOT NULL DEFAULT 0,
            kw_demand   DECIMAL(10,3) NOT NULL DEFAULT 0,
            created_at  DATETIME      NOT NULL DEFAULT NOW(),
            UNIQUE KEY uq_cust_time (cust_no, measured_at),
            INDEX idx_measured (measured_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS power_usage_daily (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            cust_no     VARCHAR(20)   NOT NULL,
            usage_date  DATE          NOT NULL,
            total_kwh   DECIMAL(12,3) NOT NULL DEFAULT 0,
            peak_kw     DECIMAL(10,3) NOT NULL DEFAULT 0,
            peak_time   TIME,
            avg_kw      DECIMAL(10,3) NOT NULL DEFAULT 0,
            updated_at  DATETIME      NOT NULL DEFAULT NOW() ON UPDATE NOW(),
            UNIQUE KEY uq_cust_date (cust_no, usage_date),
            INDEX idx_date (usage_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS power_usage_monthly (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            cust_no     VARCHAR(20)   NOT NULL,
            usage_ym    CHAR(6)       NOT NULL,
            total_kwh   DECIMAL(14,3) NOT NULL DEFAULT 0,
            bill_amount INT           NOT NULL DEFAULT 0,
            peak_kw     DECIMAL(10,3) NOT NULL DEFAULT 0,
            updated_at  DATETIME      NOT NULL DEFAULT NOW() ON UPDATE NOW(),
            UNIQUE KEY uq_cust_ym (cust_no, usage_ym)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS power_alert_history (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            cust_no     VARCHAR(20)   NOT NULL,
            alert_time  DATETIME      NOT NULL,
            level       ENUM('warning','danger') NOT NULL,
            kw_value    DECIMAL(10,3) NOT NULL,
            contract_kw DECIMAL(10,3) NOT NULL,
            usage_pct   DECIMAL(5,1)  NOT NULL,
            created_at  DATETIME      NOT NULL DEFAULT NOW(),
            UNIQUE KEY uq_alert_time (cust_no, alert_time, level),
            INDEX idx_alert_time (alert_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS power_settings (
            id                   INT AUTO_INCREMENT PRIMARY KEY,
            cust_no              VARCHAR(20)   NOT NULL DEFAULT '',
            contract_kw          DECIMAL(10,3) NOT NULL DEFAULT 500,
            warn_pct             DECIMAL(5,1)  NOT NULL DEFAULT 90.0,
            danger_pct           DECIMAL(5,1)  NOT NULL DEFAULT 95.0,
            collect_interval_min INT           NOT NULL DEFAULT 15,
            updated_at           DATETIME      NOT NULL DEFAULT NOW() ON UPDATE NOW()
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cur.execute("INSERT IGNORE INTO power_settings (id) VALUES (1)")


def _ym_offset(base_date, months_back: int) -> str:
    """base_date에서 n개월 전 YYYYMM 반환"""
    year  = base_date.year  - (months_back // 12)
    month = base_date.month - (months_back  % 12)
    if month <= 0:
        month += 12
        year  -= 1
    return f"{year}{month:02d}"


def _billing_period(base_date, months_offset: int = 0):
    """청구월(15일 기준) 시작/종료 date 반환. [start, end) 형태 (end=다음달 15일)
    months_offset: 0=이번 청구월, 1=전월, 12=전년 동월
    """
    from datetime import date
    # 이번 청구월 시작 계산
    if base_date.day >= 15:
        sy, sm = base_date.year, base_date.month
    else:
        sm = base_date.month - 1 if base_date.month > 1 else 12
        sy = base_date.year if base_date.month > 1 else base_date.year - 1

    # months_offset만큼 과거로
    sm -= months_offset
    while sm <= 0:
        sm += 12
        sy -= 1

    bill_start = date(sy, sm, 15)
    ey, em = (sy, sm + 1) if sm < 12 else (sy + 1, 1)
    bill_end = date(ey, em, 15)  # exclusive
    bill_ym  = f"{sy}{sm:02d}"   # usage_ym 키용
    return bill_start, bill_end, bill_ym


def get_power_summary(cur, cust_no: str) -> dict:
    """대시보드용 전체 요약 데이터"""
    _ensure_power_tables(cur)
    from datetime import timezone, timedelta as _td
    _KST = timezone(_td(hours=9))
    today = datetime.now(_KST).date()

    # 청구월 기준 (15일~다음달 14일)
    bill_start,  bill_end,  bill_ym  = _billing_period(today, 0)   # 이번 청구월
    prev_start,  prev_end,  prev_ym  = _billing_period(today, 1)   # 전월 청구월
    ly_start,    ly_end,    ly_ym    = _billing_period(today, 12)  # 전년 동월 청구월
    elapsed_days = (today - bill_start).days + 1  # 청구월 내 경과일수

    # ① 오늘 사용량
    cur.execute("""
        SELECT COALESCE(SUM(kwh), 0), MAX(measured_at)
        FROM power_usage_raw
        WHERE cust_no = %s AND DATE(measured_at) = %s AND measured_at <= NOW()
    """, (cust_no, today))
    row = cur.fetchone()
    today_kwh    = round(float(row[0]), 1)
    last_updated = row[1].strftime("%Y-%m-%d %H:%M") if row[1] else "-"

    def _period_kwh(start, end, ref_today):
        """기간 내 일별 사용량 합산 — power_usage_daily 우선, 당일은 raw"""
        cur.execute("""
            SELECT DATE(measured_at), ROUND(SUM(kwh),2)
            FROM power_usage_raw
            WHERE cust_no=%s AND measured_at>=%s AND measured_at<%s
            GROUP BY DATE(measured_at)
        """, (cust_no, start, end))
        raw_map = {r[0]: float(r[1]) for r in cur.fetchall()}
        cur.execute("""
            SELECT usage_date, total_kwh
            FROM power_usage_daily
            WHERE cust_no=%s AND usage_date>=%s AND usage_date<%s
        """, (cust_no, start, end))
        daily_map = {r[0]: float(r[1]) for r in cur.fetchall()}
        total = 0.0
        d = start
        while d < end and d <= ref_today:
            if d < ref_today:
                dv = daily_map.get(d, 0.0)
                total += dv if dv > 0 else raw_map.get(d, 0.0)
            else:
                total += raw_map.get(d, 0.0)
            d += timedelta(days=1)
        return round(total, 1)

    # ② 이번 청구월 누적
    month_kwh = _period_kwh(bill_start, bill_end, today)

    # ③ 설정값
    cur.execute("SELECT contract_kw, warn_pct, danger_pct FROM power_settings LIMIT 1")
    cfg        = cur.fetchone()
    contract_kw = float(cfg[0]) if cfg else 500.0
    warn_pct    = float(cfg[1]) if cfg else 90.0
    danger_pct  = float(cfg[2]) if cfg else 95.0

    # ④ 전월 청구월 대비
    prev_kwh     = _period_kwh(prev_start, prev_end, today)
    mom_diff_pct = round((month_kwh - prev_kwh) / prev_kwh * 100, 1) if prev_kwh else 0.0

    # ⑤ 전년 동월 청구월 대비 — 같은 경과일수만 비교
    ly_compare_end = ly_start + timedelta(days=elapsed_days)
    ly_kwh       = _period_kwh(ly_start, ly_compare_end, today)
    yoy_diff_pct = round((month_kwh - ly_kwh) / ly_kwh * 100, 1) if ly_kwh else None

    # ⑥ 이번 청구월 피크 kW → 피크 사용률 + 발생 일시
    cur.execute("""
        SELECT kw_demand, measured_at FROM power_usage_raw
        WHERE cust_no = %s AND measured_at >= %s AND measured_at < %s
        ORDER BY kw_demand DESC LIMIT 1
    """, (cust_no, bill_start, bill_end))
    pk_row = cur.fetchone()
    month_peak_kw  = round(float(pk_row[0]), 1) if pk_row else 0.0
    peak_at        = pk_row[1].strftime("%m/%d %H:%M") if pk_row and pk_row[1] else None
    peak_usage_pct = round(month_peak_kw / contract_kw * 100, 1) if contract_kw else 0.0

    # ⑦ 청구요금 (usage_ym = 청구월 시작 YYYYMM)
    bill_amount = prev_bill_amount = ly_bill_amount = 0
    try:
        cur.execute("SELECT bill_amount FROM power_usage_monthly WHERE cust_no=%s AND usage_ym=%s", (cust_no, bill_ym))
        r = cur.fetchone(); bill_amount = int(r[0]) if r else 0
        cur.execute("SELECT bill_amount FROM power_usage_monthly WHERE cust_no=%s AND usage_ym=%s", (cust_no, prev_ym))
        r = cur.fetchone(); prev_bill_amount = int(r[0]) if r else 0
        cur.execute("SELECT bill_amount FROM power_usage_monthly WHERE cust_no=%s AND usage_ym=%s", (cust_no, ly_ym))
        r = cur.fetchone(); ly_bill_amount = int(r[0]) if r else 0
    except Exception:
        pass

    # 단위 전력비 (원/kWh) — 전월 실청구 기준
    unit_price = 0.0
    if prev_bill_amount:
        denom = prev_kwh
        if not denom:
            try:
                cur.execute("SELECT total_kwh FROM power_usage_monthly WHERE cust_no=%s AND usage_ym=%s",
                            (cust_no, prev_ym))
                r = cur.fetchone()
                denom = float(r[0]) if r and r[0] else 0.0
            except Exception:
                denom = 0.0
        unit_price = round(prev_bill_amount / denom, 1) if denom else 0.0

    # ⑧ 최근 7일 일별 사용량 (daily 우선, 당일은 raw)
    seven_days, seven_kwh = [], []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        if d < today:
            cur.execute("SELECT total_kwh FROM power_usage_daily WHERE cust_no=%s AND usage_date=%s", (cust_no, d))
            r = cur.fetchone()
            dv = float(r[0]) if r and float(r[0]) > 0 else None
            if dv is None:
                cur.execute("SELECT COALESCE(SUM(kwh),0) FROM power_usage_raw WHERE cust_no=%s AND DATE(measured_at)=%s", (cust_no, d))
                dv = float(cur.fetchone()[0])
        else:
            cur.execute("SELECT COALESCE(SUM(kwh),0) FROM power_usage_raw WHERE cust_no=%s AND DATE(measured_at)=%s", (cust_no, d))
            dv = float(cur.fetchone()[0])
        seven_days.append(d.strftime("%m/%d"))
        seven_kwh.append(round(dv, 1))

    # ⑨ 시간대별 평균 (최근 30일) — 보조 차트용
    cur.execute("""
        SELECT HOUR(measured_at) AS hr, ROUND(AVG(kwh), 2) AS avg_kwh
        FROM power_usage_raw
        WHERE cust_no = %s AND measured_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        GROUP BY HOUR(measured_at) ORDER BY hr
    """, (cust_no,))
    hourly_map    = {int(r[0]): float(r[1]) for r in cur.fetchall()}
    hourly_labels = [f"{h:02d}시" for h in range(24)]
    hourly_data   = [round(hourly_map.get(h, 0.0), 2) for h in range(24)]

    # ⑩ 이번달 일별 사용량 + 전월동일 + 전년동일
    import calendar as _cal
    cur_year, cur_month = today.year, today.month
    days_in_month = _cal.monthrange(cur_year, cur_month)[1]
    # 전월
    pm = cur_month - 1 if cur_month > 1 else 12
    py = cur_year if cur_month > 1 else cur_year - 1
    days_in_prev_month = _cal.monthrange(py, pm)[1]
    # 전년동월
    lpy, lpm = cur_year - 1, cur_month
    days_in_ly_month = _cal.monthrange(lpy, lpm)[1]

    # 당해 일별 집계 (power_usage_daily 우선, 없으면 raw)
    cur.execute("""
        SELECT DAY(usage_date), total_kwh
        FROM power_usage_daily
        WHERE cust_no = %s AND YEAR(usage_date)=%s AND MONTH(usage_date)=%s
    """, (cust_no, cur_year, cur_month))
    cy_day_map = {int(r[0]): float(r[1]) for r in cur.fetchall()}
    # raw에서 없는 날짜 보완
    import calendar as _cal_cy
    _cy_days = _cal_cy.monthrange(cur_year, cur_month)[1]
    _cy_missing = [d for d in range(1, _cy_days + 1) if d not in cy_day_map]
    if _cy_missing:
        cur.execute("""
            SELECT DAY(measured_at), ROUND(SUM(kwh), 2)
            FROM power_usage_raw
            WHERE cust_no = %s AND YEAR(measured_at)=%s AND MONTH(measured_at)=%s
              AND DAY(measured_at) IN ({})
            GROUP BY DAY(measured_at)
        """.format(",".join(["%s"] * len(_cy_missing))),
            (cust_no, cur_year, cur_month, *_cy_missing))
        for r in cur.fetchall():
            cy_day_map[int(r[0])] = float(r[1])
    def _day_map_with_fallback(year, month):
        """power_usage_daily 우선, 없는 날짜는 power_usage_raw로 보완"""
        # 1순위: power_usage_daily (사용자가 직접 넣은 확정값)
        cur.execute("""
            SELECT DAY(usage_date), total_kwh
            FROM power_usage_daily
            WHERE cust_no = %s AND YEAR(usage_date)=%s AND MONTH(usage_date)=%s
        """, (cust_no, year, month))
        result = {int(r[0]): float(r[1]) for r in cur.fetchall()}
        # 2순위: power_usage_raw 집계 (없는 날짜만)
        import calendar as _cal2
        days = _cal2.monthrange(year, month)[1]
        missing = [d for d in range(1, days + 1) if d not in result]
        if missing:
            cur.execute("""
                SELECT DAY(measured_at), ROUND(SUM(kwh), 2)
                FROM power_usage_raw
                WHERE cust_no = %s AND YEAR(measured_at)=%s AND MONTH(measured_at)=%s
                  AND DAY(measured_at) IN ({})
                GROUP BY DAY(measured_at)
            """.format(",".join(["%s"] * len(missing))),
                (cust_no, year, month, *missing))
            for r in cur.fetchall():
                result[int(r[0])] = float(r[1])
        return result

    # 전월 일별 집계 (raw 없으면 daily 폴백)
    pm_day_map = _day_map_with_fallback(py, pm)
    # 전년동일 일별 집계 (raw 없으면 daily 폴백)
    ly_day_map = _day_map_with_fallback(lpy, lpm)

    daily_labels, daily_kwh, daily_prev_month, daily_prev_year = [], [], [], []
    for d in range(1, days_in_month + 1):
        daily_labels.append(f"{cur_month}.{d}" if d == 1 else str(d))
        # 당해: 미래 날짜는 null
        from datetime import date as _date
        is_future = _date(cur_year, cur_month, d) > today
        daily_kwh.append(None if is_future else cy_day_map.get(d, 0.0))
        # 전월: 해당 일이 전월에 존재하면 값, 없으면 null
        daily_prev_month.append(pm_day_map.get(d) if d <= days_in_prev_month else None)
        # 전년동일
        daily_prev_year.append(ly_day_map.get(d) if d <= days_in_ly_month else None)

    # 오늘 시간대별 실측 — kwh > 0인 시간만
    cur.execute("""
        SELECT HOUR(measured_at), ROUND(SUM(kwh), 2)
        FROM power_usage_raw
        WHERE cust_no = %s AND DATE(measured_at) = %s
        GROUP BY HOUR(measured_at)
        HAVING ROUND(SUM(kwh), 2) > 0
        ORDER BY HOUR(measured_at)
    """, (cust_no, today))
    _today_raw = {int(r[0]): float(r[1]) for r in cur.fetchall()}
    if _today_raw:
        _max_hr = max(_today_raw.keys())
        today_hr_labels = [f"{h:02d}시" for h in range(_max_hr + 1)]
        today_hr_data   = [round(_today_raw.get(h, 0.0), 2) for h in range(_max_hr + 1)]
    else:
        today_hr_labels = []
        today_hr_data   = []

    # ⑪ 월별 트렌드 12개월 (사용량 + 요금) — 청구월 기준
    monthly_labels, monthly_kwh_list, monthly_bill_list, monthly_ly_bill_list = [], [], [], []
    for i in range(11, -1, -1):
        ms, me, mym = _billing_period(today, i)
        cur.execute("""
            SELECT COALESCE(SUM(kwh), 0) FROM power_usage_raw
            WHERE cust_no = %s AND measured_at >= %s AND measured_at < %s
        """, (cust_no, ms, me))
        mkwh = round(float(cur.fetchone()[0]), 0)
        try:
            cur.execute("SELECT bill_amount FROM power_usage_monthly WHERE cust_no=%s AND usage_ym=%s", (cust_no, mym))
            r = cur.fetchone(); mbill = int(r[0]) if r else 0
        except Exception:
            mbill = 0
        # 전년 동월 청구요금
        _, _, ly_mym = _billing_period(today, i + 12)
        try:
            cur.execute("SELECT bill_amount FROM power_usage_monthly WHERE cust_no=%s AND usage_ym=%s", (cust_no, ly_mym))
            r = cur.fetchone(); ly_mbill = int(r[0]) if r else 0
        except Exception:
            ly_mbill = 0
        monthly_labels.append(f"{mym[2:4]}/{mym[4:]}")
        monthly_kwh_list.append(mkwh)
        monthly_bill_list.append(mbill)
        monthly_ly_bill_list.append(ly_mbill)

    # ⑫ 달력월 기준 당해/전년 월별 사용량 비교 (1~12월)
    # power_usage_raw 우선, 없으면 power_usage_monthly.total_kwh fallback
    this_year = today.year
    prev_year = this_year - 1
    cal_labels, cal_kwh, cal_ly_kwh, cal_table = [], [], [], []
    import calendar

    def _cal_month_kwh(year, month):
        # 1순위: power_usage_monthly (KEPCO 공식 확정값)
        ym_key = f"{year}{month:02d}"
        cur.execute("""
            SELECT COALESCE(total_kwh, 0) FROM power_usage_monthly
            WHERE cust_no = %s AND usage_ym = %s
        """, (cust_no, ym_key))
        r = cur.fetchone()
        if r and float(r[0]) > 0:
            return round(float(r[0]), 2)
        # 2순위: power_usage_raw 집계 (진행중인 달)
        cur.execute("""
            SELECT COALESCE(SUM(kwh), 0) FROM power_usage_raw
            WHERE cust_no = %s AND YEAR(measured_at) = %s AND MONTH(measured_at) = %s
        """, (cust_no, year, month))
        return round(float(cur.fetchone()[0]), 2)

    def _cal_month_bill(year, month):
        ym_key = f"{year}{month:02d}"
        cur.execute("""
            SELECT COALESCE(bill_amount, 0) FROM power_usage_monthly
            WHERE cust_no = %s AND usage_ym = %s
        """, (cust_no, ym_key))
        r = cur.fetchone()
        return int(r[0]) if r and int(r[0]) > 0 else 0

    cal_bill, cal_ly_bill = [], []
    for m in range(1, 13):
        last_day = calendar.monthrange(this_year, m)[1]
        ly_last_day = calendar.monthrange(prev_year, m)[1]
        cy_val = _cal_month_kwh(this_year, m)
        ly_val = _cal_month_kwh(prev_year, m)
        cy_bill = _cal_month_bill(this_year, m)
        ly_bill = _cal_month_bill(prev_year, m)
        cal_labels.append(f"{m}월")
        cal_kwh.append(cy_val)
        cal_ly_kwh.append(ly_val)
        cal_bill.append(cy_bill)
        cal_ly_bill.append(ly_bill)
        cal_table.append({
            "label":    f"{m}월({m:02d}.01~{m:02d}.{last_day:02d})",
            "cy":       cy_val,
            "ly":       ly_val,
            "cy_bill":  cy_bill,
            "ly_bill":  ly_bill,
            "ly_label": f"{m}월({m:02d}.01~{m:02d}.{ly_last_day:02d})",
        })

    # ⑬ 스마트뷰 실시간 요금 데이터
    smartview = {}
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS power_smartview (
                id INT AUTO_INCREMENT PRIMARY KEY,
                cust_no VARCHAR(20) NOT NULL,
                rt_kwh DECIMAL(12,1) NOT NULL DEFAULT 0,
                rt_bill INT NOT NULL DEFAULT 0,
                tariff VARCHAR(50) NOT NULL DEFAULT '',
                basic_rate INT NOT NULL DEFAULT 0,
                basic_bill INT NOT NULL DEFAULT 0,
                demand_kw DECIMAL(10,1) NOT NULL DEFAULT 0,
                demand_ym VARCHAR(6) NOT NULL DEFAULT '',
                max_demand_kw DECIMAL(10,1) NOT NULL DEFAULT 0,
                max_demand_date VARCHAR(20) NOT NULL DEFAULT '',
                bill_start VARCHAR(12) NOT NULL DEFAULT '',
                bill_end VARCHAR(12) NOT NULL DEFAULT '',
                rate_table_json TEXT,
                updated_at DATETIME NOT NULL DEFAULT NOW() ON UPDATE NOW(),
                UNIQUE KEY uq_cust (cust_no)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            SELECT rt_kwh, rt_bill, tariff, basic_rate, basic_bill,
                   demand_kw, demand_ym, max_demand_kw, max_demand_date,
                   bill_start, bill_end, rate_table_json, updated_at
            FROM power_smartview WHERE cust_no=%s
        """, (cust_no,))
        sv = cur.fetchone()
        if sv:
            import json as _j
            from datetime import timedelta as _td
            _prev_bill_period = ""
            try:
                _bs = datetime.strptime(sv[9], "%Y.%m.%d")
                _pe = _bs - _td(days=1)
                _ps = datetime(_pe.year, _pe.month, 15)
                _prev_bill_period = f"{_ps.strftime('%Y.%m.%d')} ~ {_pe.strftime('%Y.%m.%d')}"
            except Exception:
                pass
            smartview = {
                "rt_kwh":           float(sv[0]),
                "rt_bill":          int(sv[1]),
                "tariff":           sv[2],
                "basic_rate":       int(sv[3]),
                "basic_bill":       int(sv[4]),
                "demand_kw":        float(sv[5]),
                "demand_ym":        sv[6],
                "max_demand_kw":    float(sv[7]),
                "max_demand_date":  sv[8],
                "bill_start":       sv[9],
                "bill_end":         sv[10],
                "rate_table":       _j.loads(sv[11]) if sv[11] else [],
                "updated_at":       sv[12].strftime("%Y-%m-%d %H:%M") if sv[12] else "",
                "prev_bill_period": _prev_bill_period,
            }
    except Exception:
        pass

    return dict(
        today_kwh        = today_kwh,
        last_updated     = last_updated,
        month_kwh        = month_kwh,
        mom_diff_pct     = mom_diff_pct,
        yoy_diff_pct     = yoy_diff_pct,
        ly_kwh           = round(ly_kwh, 1),
        compare_days     = elapsed_days,
        contract_kw      = contract_kw,
        warn_pct         = warn_pct,
        danger_pct       = danger_pct,
        month_peak_kw    = month_peak_kw,
        peak_usage_pct   = peak_usage_pct,
        peak_at          = peak_at,
        bill_amount      = bill_amount,
        prev_bill_amount = prev_bill_amount,
        ly_bill_amount   = ly_bill_amount,
        unit_price       = unit_price,
        seven_days       = seven_days,
        seven_kwh        = seven_kwh,
        hourly_labels    = hourly_labels,
        hourly_data      = hourly_data,
        today_hr_labels  = today_hr_labels,
        today_hr_data    = today_hr_data,
        daily_labels     = daily_labels,
        daily_kwh        = daily_kwh,
        daily_prev_month = daily_prev_month,
        daily_prev_year  = daily_prev_year,
        cur_month        = cur_month,
        monthly_labels   = monthly_labels,
        monthly_kwh_list     = monthly_kwh_list,
        monthly_bill_list    = monthly_bill_list,
        monthly_ly_bill_list = monthly_ly_bill_list,
        cal_labels       = cal_labels,
        cal_kwh          = cal_kwh,
        cal_ly_kwh       = cal_ly_kwh,
        cal_bill         = cal_bill,
        cal_ly_bill      = cal_ly_bill,
        cal_table        = cal_table,
        this_year        = this_year,
        prev_year        = prev_year,
        smartview        = smartview,
    )


def get_history_data(cur, cust_no: str, start_date: str, end_date: str) -> dict:
    """기간별 조회: 15분단위 + 일별 집계"""
    # 15분단위 line chart
    cur.execute("""
        SELECT measured_at, kw_demand, kwh
        FROM power_usage_raw
        WHERE cust_no = %s
          AND DATE(measured_at) BETWEEN %s AND %s
        ORDER BY measured_at
    """, (cust_no, start_date, end_date))
    rows = cur.fetchall()
    line_labels = [r[0].strftime("%m/%d %H:%M") for r in rows]
    line_kw     = [round(float(r[1]), 2) for r in rows]
    line_kwh    = [round(float(r[2]), 3) for r in rows]

    # 일별 집계 테이블
    cur.execute("""
        SELECT usage_date, total_kwh, peak_kw, peak_time, avg_kw
        FROM power_usage_daily
        WHERE cust_no = %s
          AND usage_date BETWEEN %s AND %s
        ORDER BY usage_date DESC
    """, (cust_no, start_date, end_date))
    daily_rows = [
        {
            "date":      str(r[0]),
            "total_kwh": round(float(r[1]), 1),
            "peak_kw":   round(float(r[2]), 1),
            "peak_time": str(r[3])[:5] if r[3] else "-",
            "avg_kw":    round(float(r[4]), 1),
        }
        for r in cur.fetchall()
    ]

    return dict(
        line_labels=line_labels,
        line_kw=line_kw,
        line_kwh=line_kwh,
        daily_rows=daily_rows,
    )


def get_alert_list(cur, cust_no: str, page: int = 1, per_page: int = 20) -> dict:
    """경보 이력 페이징"""
    offset = (page - 1) * per_page
    cur.execute("""
        SELECT COUNT(*) FROM power_alert_history WHERE cust_no = %s
    """, (cust_no,))
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT alert_time, level, kw_value, contract_kw, usage_pct
        FROM power_alert_history
        WHERE cust_no = %s
        ORDER BY alert_time DESC
        LIMIT %s OFFSET %s
    """, (cust_no, per_page, offset))
    rows = [
        {
            "alert_time":  r[0].strftime("%Y-%m-%d %H:%M"),
            "level":       r[1],
            "kw_value":    round(float(r[2]), 1),
            "contract_kw": round(float(r[3]), 1),
            "usage_pct":   round(float(r[4]), 1),
        }
        for r in cur.fetchall()
    ]

    total_pages = max(1, (total + per_page - 1) // per_page)
    return dict(rows=rows, total=total, page=page, total_pages=total_pages)


# ── 데모 데이터 ───────────────────────────────────────────
def make_demo_summary() -> dict:
    rng = random.Random(datetime.now().strftime("%Y%m%d"))
    today = datetime.now()

    seven_kwh = [round(rng.uniform(300, 700), 1) for _ in range(7)]
    seven_days = [
        (today - timedelta(days=i)).strftime("%m/%d")
        for i in range(6, -1, -1)
    ]

    # 시간대별 평균: 주간 높고 야간 낮게
    hourly_data = []
    for h in range(24):
        if 8 <= h < 18:
            hourly_data.append(round(rng.uniform(40, 90), 1))
        elif 6 <= h < 8 or 18 <= h < 21:
            hourly_data.append(round(rng.uniform(20, 50), 1))
        else:
            hourly_data.append(round(rng.uniform(5, 20), 1))

    # 오늘 15분 단위
    intraday_labels, intraday_data = [], []
    for h in range(24):
        for m in (0, 15, 30, 45):
            intraday_labels.append(f"{h:02d}:{m:02d}")
            base = 70 if 8 <= h < 18 else 15
            intraday_data.append(round(rng.uniform(base * 0.7, base * 1.3), 1))

    contract_kw = 500.0
    current_kw  = round(rng.uniform(30, 80), 1)

    return dict(
        today_kwh    = round(sum(intraday_data) * 0.25, 1),   # kW * 15min
        month_kwh    = round(rng.uniform(8000, 15000), 1),
        current_kw   = current_kw,
        last_updated = today.strftime("%Y-%m-%d %H:%M"),
        contract_kw  = contract_kw,
        warn_pct     = 90.0,
        danger_pct   = 95.0,
        usage_pct    = round(current_kw / contract_kw * 100, 1),
        mom_diff_pct = round(rng.uniform(-15, 15), 1),
        seven_days   = seven_days,
        seven_kwh    = seven_kwh,
        hourly_labels= [f"{h:02d}시" for h in range(24)],
        hourly_data  = hourly_data,
        bill_amount      = 0,
        prev_bill_amount = round(rng.uniform(60000000, 90000000)),
        unit_price       = round(rng.uniform(150, 200), 1),
        yoy_diff_pct     = round(rng.uniform(-15, 15), 1),
        month_peak_kw    = round(rng.uniform(400, 480), 1),
        peak_usage_pct   = round(rng.uniform(80, 96), 1),
        today_hr_labels  = [f"{h:02d}시" for h in range(16)],
        today_hr_data    = [round(rng.uniform(300, 550), 1) for _ in range(16)],
        monthly_labels   = [f"{(today - timedelta(days=30*i)).strftime('%y/%m')}" for i in range(11, -1, -1)],
        monthly_kwh_list = [round(rng.uniform(120000, 200000)) for _ in range(12)],
        monthly_bill_list= [round(rng.uniform(60000000, 90000000)) for _ in range(12)],
        anomalies        = [],
    )


def make_demo_history(start_date: str, end_date: str) -> dict:
    rng = random.Random(start_date)
    try:
        s = datetime.strptime(start_date, "%Y-%m-%d")
        e = datetime.strptime(end_date,   "%Y-%m-%d")
    except ValueError:
        s = e = datetime.now()

    line_labels, line_kw, line_kwh = [], [], []
    daily_rows = []
    d = s
    while d <= e:
        peak = 0.0
        day_kwh = 0.0
        for h in range(24):
            for m in (0, 15, 30, 45):
                base = 60 if 8 <= h < 18 else 12
                kw = round(rng.uniform(base * 0.7, base * 1.3), 2)
                kwh = round(kw * 0.25, 3)
                line_labels.append(f"{d.strftime('%m/%d')} {h:02d}:{m:02d}")
                line_kw.append(kw)
                line_kwh.append(kwh)
                day_kwh += kwh
                if kw > peak:
                    peak = kw
        daily_rows.append({
            "date":      d.strftime("%Y-%m-%d"),
            "total_kwh": round(day_kwh, 1),
            "peak_kw":   round(peak, 1),
            "peak_time": "14:15",
            "avg_kw":    round(day_kwh / 24, 1),
        })
        d += timedelta(days=1)

    return dict(
        line_labels=line_labels,
        line_kw=line_kw,
        line_kwh=line_kwh,
        daily_rows=daily_rows,
    )


def make_demo_alerts() -> dict:
    rng = random.Random(42)
    today = datetime.now()
    rows = []
    for i in range(15):
        dt = today - timedelta(hours=i * 8 + rng.randint(0, 7))
        kw = round(rng.uniform(440, 490), 1)
        rows.append({
            "alert_time":  dt.strftime("%Y-%m-%d %H:%M"),
            "level":       "danger" if kw > 475 else "warning",
            "kw_value":    kw,
            "contract_kw": 500.0,
            "usage_pct":   round(kw / 500.0 * 100, 1),
        })
    return dict(rows=rows, total=15, page=1, total_pages=1)
