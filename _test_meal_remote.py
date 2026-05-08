import pymysql
from datetime import datetime, timedelta

MARIA = dict(host='127.0.0.1', port=3307, user='root', password='7602Mr123$', database='attendance', charset='utf8mb4')

try:
    conn = pymysql.connect(**MARIA)
    cur = conn.cursor()
    today = datetime.now()
    DOW_KR = ['월','화','수','목','금','토','일']

    print(f"Today: {today.strftime('%Y-%m-%d')} ({DOW_KR[today.weekday()]})")

    for i in range(7):
        d = today + timedelta(days=i)
        ym = d.strftime('%Y-%m')
        cur.execute("SELECT dept, meal_type, `count` FROM meal_count WHERE `year_month`=%s AND `day`=%s", (ym, d.day))
        rows = cur.fetchall()
        if rows:
            print(f"  {d.strftime('%m/%d')} ({DOW_KR[d.weekday()]}): {rows}")
        else:
            print(f"  {d.strftime('%m/%d')} ({DOW_KR[d.weekday()]}): (no data)")
    conn.close()
    print("DB query OK")
except Exception as e:
    print(f"ERROR: {e}")
