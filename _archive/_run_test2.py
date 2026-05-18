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

# Upload test script via base64
with open('/Users/changkooji/attendance/_test_meal2.py', 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()

# Write to NAS filesystem
_, stdout, _ = c.exec_command(f"echo '{b64}' | base64 -d > /tmp/_test_meal2.py && echo OK")
print("write:", stdout.read().decode().strip())

# Docker cp into container
print("cp:", sudo_cmd("docker cp /tmp/_test_meal2.py attendance-app:/tmp/_test_meal2.py"))

# Run inside container
print("\n=== Running test ===")
print(sudo_cmd("docker exec attendance-app python3 /tmp/_test_meal2.py"))

c.close()
