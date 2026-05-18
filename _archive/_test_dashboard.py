import paramiko, base64

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

# Restart the container to ensure fresh code is loaded
print("Restarting container...")
print(sudo_cmd("docker restart attendance-app"))

import time
time.sleep(12)

# Check if app is up
print("\n=== Healthcheck ===")
print(sudo_cmd("docker exec attendance-app python3 -c 'import urllib.request; r=urllib.request.urlopen(\"http://localhost:5050/\"); print(r.status)'"))

# Check the rendered dashboard for meal content
print("\n=== Dashboard meal section check ===")
# Use wget/python to fetch dashboard and search for meal-related content
test_code = """
import urllib.request
try:
    r = urllib.request.urlopen("http://localhost:5050/")
    html = r.read().decode()
    # Check if meal data appears
    if '식수 데이터 없음' in html:
        print("FOUND: 식수 데이터 없음 (meal data NOT showing)")
    elif '식수현황' in html:
        print("FOUND: 식수현황 header present")
        if '44' in html:
            print("FOUND: value 44 in page")
        if '석식' in html or '중식' in html:
            print("FOUND: meal types in page")
    # Check if meal_week was passed (look for date columns)
    if '05/02' in html:
        print("FOUND: 05/02 date column")
    if '05/04' in html:
        print("FOUND: 05/04 date column")
except Exception as e:
    print(f"Error: {e}")
"""

b64 = base64.b64encode(test_code.encode()).decode()
_, stdout, _ = c.exec_command(f"echo '{b64}' | base64 -d > /tmp/_test_dash.py")
stdout.read()
sudo_cmd("docker cp /tmp/_test_dash.py attendance-app:/tmp/_test_dash.py")
print(sudo_cmd("docker exec attendance-app python3 /tmp/_test_dash.py"))

c.close()
