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

# Read the test script and base64 encode it
with open('/Users/changkooji/attendance/_test_meal_remote.py', 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()

# Write via base64 decode on NAS
_, stdout, _ = c.exec_command(f"echo '{b64}' | base64 -d > /tmp/_test_meal.py")
stdout.read()

# Copy into container and run
print(sudo_cmd("docker cp /tmp/_test_meal.py attendance-app:/tmp/_test_meal.py"))
out = sudo_cmd("docker exec attendance-app python3 /tmp/_test_meal.py")
print(out)

c.close()
