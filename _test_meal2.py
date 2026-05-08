import pymysql, os
from datetime import datetime, timedelta

MARIA = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", "3307")),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", "7602Mr123$$"),
    "db": os.environ.get("DB_NAME", "attendance"),
    "charset": "utf8mb4",
}

try:
    conn2 = pymysql.connect(**MARIA)
    cur2 = conn2.cursor()
    today = datetime.now()
    DOW_KR2 = ["월","화","수","목","금","토","일"]
    print(f"today={today.strftime('%Y-%m-%d')}")
    meal_week = []
    for i in range(7):
        d = today + timedelta(days=i)
        ym = d.strftime("%Y-%m")
        cur2.execute("SELECT dept, meal_type, `count` FROM meal_count WHERE `year_month`=%s AND `day`=%s", (ym, d.day))
        day_meals = {}
        for dept, mtype, cnt in cur2.fetchall():
            day_meals.setdefault(mtype, {})[dept] = cnt
        print(f"  {d.strftime('%m/%d')} day={d.day} ym={ym} meals={day_meals}")
        meal_week.append({"date": d.strftime("%m/%d"), "dow": DOW_KR2[d.weekday()], "day": d.day, "meals": day_meals})
    conn2.close()
    print(f"meal_week len={len(meal_week)}")
    has_data = any(m['meals'] for m in meal_week)
    print(f"has_data={has_data}")
except Exception as e:
    print(f"EXCEPTION: {e}")
    import traceback
    traceback.print_exc()
