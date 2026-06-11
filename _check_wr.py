import os, paramiko
from dotenv import load_dotenv
load_dotenv()
H=os.environ["NAS_HOST"]; U=os.environ["NAS_USER"]; P=os.environ["NAS_PASS"]; S=os.environ["NAS_SUDO"]
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
key=os.path.expanduser("~/.ssh/id_tams_nas")
try:
    if os.path.exists(key): c.connect(H,port=22,username=U,key_filename=key,timeout=15)
    else: c.connect(H,port=22,username=U,password=P,timeout=15)
except Exception:
    c.connect(H,port=22,username=U,password=P,timeout=15)
def sudo(cmd):
    if H.endswith(".5"):
        full=f"sh -c 'PATH=/usr/local/bin:$PATH {cmd}' 2>&1"
    else:
        full=f"echo '{S}' | sudo -S sh -c 'PATH=/usr/local/bin:$PATH {cmd}' 2>&1"
    _,o,_=c.exec_command(full); out=o.read().decode()
    return out.replace("Password: ","",1)
print("=== 컨테이너 내 wrTable thead th 규칙 존재여부 ===")
print(sudo("docker exec attendance-app grep -n 'wrTable thead th' /app/templates/work_report.html"))
print("=== 파일 mtime ===")
print(sudo("docker exec attendance-app stat -c '%y' /app/templates/work_report.html"))
c.close()
