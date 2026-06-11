import os, json, subprocess, sys
from dotenv import load_dotenv
load_dotenv("/Users/changkooji/attendance/.env")

TARGET=10; MAXSEC=240
print(f"=== {TARGET}개 카운트 대기 (최대 {MAXSEC}초) ===", flush=True)
p=subprocess.Popen(["mosquitto_sub","-h",os.environ["DB_HOST"],"-p","1883",
    "-t","mes/count","-W",str(MAXSEC),"-v"],stdout=subprocess.PIPE,text=True,bufsize=1)
n=0
for line in p.stdout:
    line=line.strip()
    if not line.startswith("mes/count "): continue
    try: c=int(json.loads(line[10:]).get("count",0))
    except: continue
    if c>0:
        n+=1
        print(f"  카운트 {n}/{TARGET}", flush=True)
        if n>=TARGET:
            p.terminate()
            print(f"\n🔔 10개 도달! {TARGET}개 카운트가 들어왔습니다.")
            sys.exit(0)
print(f"\n⏱ 시간초과 — {n}/{TARGET}개만 들어옴 (생산이 느림)")
