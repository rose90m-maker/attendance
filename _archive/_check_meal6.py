import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.100.11', port=22, username='rose90m', password='7602Mr123$$')

cmd = "echo '7602Mr123$$' | sudo -S sh -c 'PATH=/usr/local/bin:$PATH docker exec attendance-app wc -l /app/app.py'"
_, stdout, _ = c.exec_command(cmd)
out = stdout.read().decode().strip().split('\n')
print(f"Lines: {out[-1]}")

cmd2 = "echo '7602Mr123$$' | sudo -S sh -c 'PATH=/usr/local/bin:$PATH docker exec attendance-app grep -n meal_week /app/app.py'"
_, stdout2, _ = c.exec_command(cmd2)
out2 = stdout2.read().decode()
for line in out2.split('\n'):
    if line.strip() and 'Password' not in line:
        print(line)

c.close()
