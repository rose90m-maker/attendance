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

# Trigger dashboard load
print("=== Triggering dashboard ===")
print(sudo_cmd("docker exec attendance-app curl -s -o /dev/null -w '%{http_code}' http://localhost:5050/"))

time.sleep(2)

# Check logs for meal_week error
print("\n=== Recent logs (meal related) ===")
out = sudo_cmd("docker logs attendance-app --tail 100 2>&1 | grep -i 'meal\\|error\\|Error\\|Traceback'")
print(out if out else "(no meal/error logs found)")

# Also check: is the container running app_maria.py or app.py?
print("\n=== Container start command ===")
print(sudo_cmd("docker inspect attendance-app --format='{{.Config.Cmd}}'"))
print(sudo_cmd("docker inspect attendance-app --format='{{.Config.Entrypoint}}'"))

# Direct test: run the meal query inside container
print("\n=== Direct meal query test ===")
test_code = '''
import pymysql
conn = pymysql.connect(host="127.0.0.1", port=3307, user="root", password="7602Mr123$", database="attendance", charset="utf8mb4")
cur = conn.cursor()
cur.execute("SELECT dept, meal_type, `count`, `year_month`, `day` FROM meal_count WHERE `year_month`='2026-05' LIMIT 10")
for row in cur.fetchall():
    print(row)
conn.close()
'''
print(sudo_cmd(f"docker exec attendance-app python3 -c \"{test_code}\""))

c.close()
