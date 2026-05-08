import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.100.11', port=22, username='rose90m', password='7602Mr123$$')

# Check if file exists and its size
cmd = "echo '7602Mr123$$' | sudo -S docker exec attendance-app wc -l /app/app.py 2>&1"
_, stdout, _ = c.exec_command(cmd)
print("Lines:", stdout.read().decode().strip())

# Search for meal
cmd2 = "echo '7602Mr123$$' | sudo -S docker exec attendance-app grep -n meal /app/app.py 2>&1"
_, stdout2, _ = c.exec_command(cmd2)
out = stdout2.read().decode()
for line in out.split('\n'):
    if 'Password' not in line and line.strip():
        print(line)

c.close()
