import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.100.11', port=22, username='rose90m', password='7602Mr123$$')

# Check if meal_week exists at all in deployed code
cmd = "echo '7602Mr123$$' | sudo -S sh -c 'PATH=/usr/local/bin:$PATH docker exec attendance-app grep -c meal_week /app/app.py' 2>&1"
_, stdout, _ = c.exec_command(cmd)
out = stdout.read().decode()
for line in out.split('\n'):
    if 'Password' not in line and line.strip():
        print(f"meal_week count: {line}")

# Check render_template line
cmd2 = "echo '7602Mr123$$' | sudo -S sh -c 'PATH=/usr/local/bin:$PATH docker exec attendance-app grep -n render_template.*dashboard /app/app.py' 2>&1"
_, stdout2, _ = c.exec_command(cmd2)
out2 = stdout2.read().decode()
for line in out2.split('\n'):
    if 'Password' not in line and line.strip():
        print(line)

c.close()
