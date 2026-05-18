import paramiko, time

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.100.11', port=22, username='rose90m', password='7602Mr123$$')

def sudo_cmd(cmd):
    full = f"echo '7602Mr123$$' | sudo -S sh -c 'PATH=/usr/local/bin:$PATH {cmd}' 2>&1"
    _, stdout, _ = c.exec_command(full)
    out = stdout.read().decode().strip()
    if out.startswith("Password: "):
        out = out[len("Password: "):]
    return out.strip()

# Write a test script to the container
test_script = """
import pymysql, os
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
    print("DB OK")
except Exception as e:
    print(f"ERROR: {e}")
"""

# Write script to container
sudo_cmd(f"docker exec attendance-app sh -c 'cat > /tmp/test_meal.py << PYEOF\n{test_script}\nPYEOF'")

# Run it
print(sudo_cmd("docker exec attendance-app python3 /tmp/test_meal.py"))

# Also get last 30 lines of logs after restart
print("\n=== Docker logs (last 30, stderr) ===")
out = sudo_cmd("docker logs attendance-app --tail 30 2>&1")
for line in out.split('\n'):
    if 'meal' in line.lower() or 'error' in line.lower() or 'traceback' in line.lower():
        print(line)

c.close()
