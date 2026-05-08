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

print("=== meal_week in container template ===")
print(sudo_cmd("docker exec attendance-app grep -n meal_week /app/templates/dashboard.html"))

print("\n=== 식수 데이터 없음 in container template ===")
print(sudo_cmd("docker exec attendance-app grep -n '식수' /app/templates/dashboard.html"))

c.close()
