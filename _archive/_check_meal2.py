import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.100.11', port=22, username='rose90m', password='7602Mr123$$')

# Check deployed meal_week code
cmd = "echo '7602Mr123$$' | sudo -S sh -c 'PATH=/usr/local/bin:$PATH docker exec attendance-app grep -n meal_week /app/app.py' 2>&1"
_, stdout, _ = c.exec_command(cmd)
out = stdout.read().decode()
for line in out.split('\n'):
    if 'Password' not in line and line.strip():
        print(line)

print("\n--- meal_week code block ---")
cmd2 = "echo '7602Mr123$$' | sudo -S sh -c 'PATH=/usr/local/bin:$PATH docker exec attendance-app sed -n \"/meal_week = /,/meal_week error/p\" /app/app.py' 2>&1"
_, stdout2, _ = c.exec_command(cmd2)
out2 = stdout2.read().decode()
for line in out2.split('\n'):
    if 'Password' not in line and line.strip():
        print(line)

c.close()
