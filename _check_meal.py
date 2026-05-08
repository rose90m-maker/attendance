import paramiko, base64

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.100.11', port=22, username='rose90m', password='7602Mr123$$')

# Check docker logs for meal error
cmd = "echo '7602Mr123$$' | sudo -S sh -c 'PATH=/usr/local/bin:$PATH docker logs attendance-app --tail 30 2>&1' 2>&1"
_, stdout, _ = c.exec_command(cmd)
out = stdout.read().decode()
for line in out.split('\n'):
    if 'Password' not in line and line.strip():
        print(line)
c.close()
