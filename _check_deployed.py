import paramiko

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

print("=== app_maria.py lines ===")
print(sudo_cmd("docker exec attendance-app wc -l /app/app_maria.py"))

print("\n=== meal_week in app_maria.py ===")
out = sudo_cmd("docker exec attendance-app grep -n meal_week /app/app_maria.py")
print(out if out else "(not found)")

print("\n=== app.py lines ===")
print(sudo_cmd("docker exec attendance-app wc -l /app/app.py"))

print("\n=== meal_week in app.py ===")
out2 = sudo_cmd("docker exec attendance-app grep -n meal_week /app/app.py")
print(out2 if out2 else "(not found)")

c.close()
