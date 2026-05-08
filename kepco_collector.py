"""
KEPCO EDS Open API 전력 데이터 수집 배치
========================================
사용법:
  python kepco_collector.py              # 오늘 데이터 수집
  python kepco_collector.py today        # 오늘 데이터 수집
  python kepco_collector.py range YYYYMMDD YYYYMMDD  # 기간 수집
  python kepco_collector.py init_db      # DB 테이블 생성만

크론탭 (NAS Docker exec):
  */15 * * * * docker exec attendance-app python /app/kepco_collector.py today
"""
import os, sys, json, time, urllib.request, urllib.parse
from datetime import datetime, timedelta

import pymysql
from dotenv import load_dotenv

load_dotenv()

# ── 설정 ──────────────────────────────────────────────────
KEPCO_KEY  = os.environ.get("KEPCO_API_KEY", "")
KEPCO_CUST = os.environ.get("KEPCO_CUST_NO", "")
KEPCO_BASE = "https://openapi.kepco.co.kr/service/EdsuSendApi"

MARIA = {
    "host":     os.environ.get("DB_HOST",     "127.0.0.1"),
    "port":     int(os.environ.get("DB_PORT", "3307")),
    "user":     os.environ.get("DB_USER",     "root"),
    "password": os.environ.get("DB_PASSWORD", "7602mr"),
    "db":       os.environ.get("DB_NAME",     "attendance"),
    "charset":  "utf8mb4",
}

MAX_RETRY = 3

# ── DB 마이그레이션 SQL ────────────────────────────────────
INIT_SQL = """
CREATE TABLE IF NOT EXISTS power_usage_raw (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    cust_no       VARCHAR(20)   NOT NULL,
    measured_at   DATETIME      NOT NULL,
    kwh           DECIMAL(10,3) NOT NULL DEFAULT 0,
    kw_demand     DECIMAL(10,3) NOT NULL DEFAULT 0,
    created_at    DATETIME      NOT NULL DEFAULT NOW(),
    UNIQUE KEY uq_cust_time (cust_no, measured_at),
    INDEX idx_measured (measured_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS power_usage_monthly (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    cust_no     VARCHAR(20)   NOT NULL,
    usage_ym    CHAR(6)       NOT NULL,
    total_kwh   DECIMAL(14,3) NOT NULL DEFAULT 0,
    bill_amount INT           NOT NULL DEFAULT 0,
    peak_kw     DECIMAL(10,3) NOT NULL DEFAULT 0,
    updated_at  DATETIME      NOT NULL DEFAULT NOW() ON UPDATE NOW(),
    UNIQUE KEY uq_cust_ym (cust_no, usage_ym)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS power_alert_history (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    cust_no     VARCHAR(20)   NOT NULL,
    alert_time  DATETIME      NOT NULL,
    level       ENUM('warning','danger') NOT NULL,
    kw_value    DECIMAL(10,3) NOT NULL,
    contract_kw DECIMAL(10,3) NOT NULL,
    usage_pct   DECIMAL(5,1)  NOT NULL,
    created_at  DATETIME      NOT NULL DEFAULT NOW(),
    UNIQUE KEY uq_alert (cust_no, alert_time, level),
    INDEX idx_alert_time (alert_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS power_settings (
    id                   INT AUTO_INCREMENT PRIMARY KEY,
    cust_no              VARCHAR(20)   NOT NULL DEFAULT '',
    contract_kw          DECIMAL(10,3) NOT NULL DEFAULT 500,
    warn_pct             DECIMAL(5,1)  NOT NULL DEFAULT 90.0,
    danger_pct           DECIMAL(5,1)  NOT NULL DEFAULT 95.0,
    collect_interval_min INT           NOT NULL DEFAULT 15,
    updated_at           DATETIME      NOT NULL DEFAULT NOW() ON UPDATE NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


# ── KEPCO API 호출 ─────────────────────────────────────────
def kepco_get(endpoint: str, params: dict, retry: int = MAX_RETRY) -> dict:
    params_full = {
        "serviceKey": KEPCO_KEY,
        "custNo":     KEPCO_CUST,
        "returnType": "json",
    }
    params_full.update(params)
    url = f"{KEPCO_BASE}/{endpoint}?{urllib.parse.urlencode(params_full)}"
    last_err = None
    for attempt in range(retry):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except Exception as e:
            last_err = e
            if attempt < retry - 1:
                time.sleep(2 ** attempt)   # 지수 백오프: 1s → 2s
    raise RuntimeError(f"KEPCO API 실패 ({endpoint}): {last_err}")


def _items(resp: dict):
    """응답에서 item 리스트 추출 (dict 단건도 list로 변환)"""
    try:
        items = resp["response"]["body"]["items"]["item"]
        return items if isinstance(items, list) else [items]
    except (KeyError, TypeError):
        return []


# ── 수집 함수 ─────────────────────────────────────────────
def collect_day(date_str: str) -> list[dict]:
    """일단위 96개 수집 (API #6: getMeteringInfo)"""
    resp = kepco_get("getMeteringInfo", {"searchDate": date_str})
    records = []
    for it in _items(resp):
        t = str(it.get("meteringTime", "00:00"))[:5]  # "HH:MM"
        try:
            dt = datetime.strptime(f"{date_str} {t}", "%Y%m%d %H:%M")
        except ValueError:
            continue
        records.append({
            "measured_at": dt,
            "kwh":         float(it.get("kwh",  0) or 0),
            "kw_demand":   float(it.get("kw",   0) or 0),
        })
    return records


# ── DB 저장 함수 ──────────────────────────────────────────
def save_raw(cur, records: list[dict]):
    """중복 무시 INSERT"""
    if not records:
        return 0
    sql = """INSERT IGNORE INTO power_usage_raw
             (cust_no, measured_at, kwh, kw_demand)
             VALUES (%s, %s, %s, %s)"""
    rows = [
        (KEPCO_CUST, r["measured_at"], r["kwh"], r["kw_demand"])
        for r in records
    ]
    cur.executemany(sql, rows)
    return cur.rowcount


def aggregate_daily(cur, date_str: str):
    """power_usage_raw → power_usage_daily 집계 (UPSERT)"""
    # 피크 시각 서브쿼리로 정확하게
    cur.execute("""
        INSERT INTO power_usage_daily
            (cust_no, usage_date, total_kwh, peak_kw, peak_time, avg_kw)
        SELECT
            cust_no,
            DATE(measured_at)    AS usage_date,
            ROUND(SUM(kwh), 3)   AS total_kwh,
            ROUND(MAX(kw_demand), 3) AS peak_kw,
            (SELECT TIME(measured_at) FROM power_usage_raw r2
             WHERE r2.cust_no = r.cust_no
               AND DATE(r2.measured_at) = DATE(r.measured_at)
             ORDER BY kw_demand DESC LIMIT 1) AS peak_time,
            ROUND(AVG(kw_demand), 3) AS avg_kw
        FROM power_usage_raw r
        WHERE cust_no = %s AND DATE(measured_at) = %s
        GROUP BY cust_no, DATE(measured_at)
        ON DUPLICATE KEY UPDATE
            total_kwh  = VALUES(total_kwh),
            peak_kw    = VALUES(peak_kw),
            peak_time  = VALUES(peak_time),
            avg_kw     = VALUES(avg_kw),
            updated_at = NOW()
    """, (KEPCO_CUST, date_str))


def aggregate_monthly(cur, ym: str):
    """power_usage_daily → power_usage_monthly 집계 (UPSERT)"""
    cur.execute("""
        INSERT INTO power_usage_monthly
            (cust_no, usage_ym, total_kwh, peak_kw)
        SELECT
            cust_no,
            DATE_FORMAT(usage_date, '%%Y%%m') AS usage_ym,
            ROUND(SUM(total_kwh), 3)           AS total_kwh,
            MAX(peak_kw)                        AS peak_kw
        FROM power_usage_daily
        WHERE cust_no = %s AND DATE_FORMAT(usage_date, '%%Y%%m') = %s
        GROUP BY cust_no, usage_ym
        ON DUPLICATE KEY UPDATE
            total_kwh  = VALUES(total_kwh),
            peak_kw    = VALUES(peak_kw),
            updated_at = NOW()
    """, (KEPCO_CUST, ym))


def check_and_save_alerts(cur, contract_kw: float, warn_pct: float, danger_pct: float):
    """최신 1건으로 경보 체크 & 중복 없이 저장"""
    cur.execute("""
        SELECT measured_at, kw_demand
        FROM power_usage_raw
        WHERE cust_no = %s
        ORDER BY measured_at DESC
        LIMIT 1
    """, (KEPCO_CUST,))
    row = cur.fetchone()
    if not row:
        return None
    mtime, kw = row
    kw = float(kw)
    pct = round(kw / contract_kw * 100, 1) if contract_kw else 0
    level = None
    if pct >= danger_pct:
        level = "danger"
    elif pct >= warn_pct:
        level = "warning"
    if level:
        cur.execute("""
            INSERT IGNORE INTO power_alert_history
                (cust_no, alert_time, level, kw_value, contract_kw, usage_pct)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (KEPCO_CUST, mtime, level, kw, contract_kw, pct))
    return {"time": mtime, "kw": kw, "pct": pct, "level": level}


# ── 메인 ──────────────────────────────────────────────────
def init_db(conn):
    cur = conn.cursor()
    for stmt in INIT_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    # 설정 기본행 (없으면 삽입)
    cur.execute("SELECT COUNT(*) FROM power_settings")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO power_settings (id) VALUES (1)")
    conn.commit()
    print("✅ DB 테이블 초기화 완료")


def run(dates: list[str], conn):
    cur = conn.cursor()

    # 설정 로드
    cur.execute("SELECT contract_kw, warn_pct, danger_pct FROM power_settings LIMIT 1")
    row = cur.fetchone()
    contract_kw, warn_pct, danger_pct = (float(row[0]), float(row[1]), float(row[2])) if row else (500, 90.0, 95.0)

    months_updated = set()
    for d in dates:
        try:
            records = collect_day(d)
            saved   = save_raw(cur, records)
            aggregate_daily(cur, d)
            months_updated.add(d[:6])
            conn.commit()
            print(f"  ✅ {d}: API {len(records)}건, DB {saved}건 저장")
        except Exception as e:
            conn.rollback()
            print(f"  ❌ {d}: {e}")

    for ym in months_updated:
        aggregate_monthly(cur, ym)

    alert = check_and_save_alerts(cur, contract_kw, warn_pct, danger_pct)
    if alert and alert["level"]:
        print(f"  🚨 경보 [{alert['level'].upper()}] {alert['pct']}% ({alert['kw']} kW)")
    conn.commit()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "today"

    conn = pymysql.connect(**MARIA)

    # DB 초기화 (테이블 없으면 생성)
    init_db(conn)

    if mode == "init_db":
        conn.close()
        sys.exit(0)

    if not KEPCO_KEY or not KEPCO_CUST:
        print("⚠️  KEPCO_API_KEY 또는 KEPCO_CUST_NO 미설정 — .env 확인")
        conn.close()
        sys.exit(1)

    today = datetime.now()

    if mode in ("today", ""):
        dates = [today.strftime("%Y%m%d")]
    elif mode == "range":
        start = datetime.strptime(sys.argv[2], "%Y%m%d")
        end   = datetime.strptime(sys.argv[3], "%Y%m%d")
        dates = [
            (start + timedelta(days=i)).strftime("%Y%m%d")
            for i in range((end - start).days + 1)
        ]
    else:
        print(f"알 수 없는 모드: {mode}")
        sys.exit(1)

    print(f"📡 수집 시작: {len(dates)}일치 ({dates[0]} ~ {dates[-1]})")
    run(dates, conn)
    conn.close()
    print("완료")
