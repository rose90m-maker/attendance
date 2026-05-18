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

# Check MARIA dict in deployed code
print("=== MARIA config ===")
out = sudo_cmd("docker exec attendance-app grep -n MARIA /app/app_maria.py")
print(out)

print("\n=== def _conn ===")
out2 = sudo_cmd("docker exec attendance-app grep -n def._conn /app/app_maria.py")
print(out2)

print("\n=== host in code ===")
out3 = sudo_cmd("docker exec attendance-app grep -n 3307 /app/app_maria.py")
print(out3)

c.close()
