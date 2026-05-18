import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.100.11', port=22, username='rose90m', password='7602Mr123$$')

# Check file size
cmd = "echo '7602Mr123$$' | sudo -S sh -c 'PATH=/usr/local/bin:$PATH docker exec attendance-app wc -l /app/app.py'"
_, stdout, _ = c.exec_command(cmd)
out = stdout.read().decode().strip()
print(f"Lines: {out.split(chr(10))[-1]}")

# Search for meal in deployed code
cmd2 = "echo '7602Mr123$$' | sudo -S sh -c 'PATH=/usr/local/bin:$PATH docker exec attendance-app grep -n meal /app/app.py'"
_, stdout2, stderr2 = c.exec_command(cmd2)
out2 = stdout2.read().decode()
err2 = stderr2.read().decode()
for line in out2.split('\n'):
    if line.strip():
        print(line)
if err2.strip():
    print(f"STDERR: {err2.strip()}")

c.close()
