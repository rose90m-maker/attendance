import os, json, subprocess
from dotenv import load_dotenv
load_dotenv("/Users/changkooji/attendance/.env")
import pymysql
def dbsum():
    c=pymysql.connect(host=os.environ["DB_HOST"],port=int(os.environ["DB_PORT"]),user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],database=os.environ["DB_NAME"],connect_timeout=8).cursor()
    c.execute("SELECT COALESCE(SUM(count),0) FROM mes_label_count WHERE device_id='Line_A' AND DATE(recorded_at)=CURDATE()")
    return int(c.fetchone()[0])

WIN=30
print(f"=== {WIN}초간 7개 통과시켜 주세요 (펄스 실시간) ===\n", flush=True)
s0=dbsum()
p=subprocess.Popen(["mosquitto_sub","-h",os.environ["DB_HOST"],"-p","1883",
    "-t","mes/log/Line_A","-t","mes/count","-W",str(WIN),"-v"],stdout=subprocess.PIPE,text=True,bufsize=1)
acc=rej=pub=0
for line in p.stdout:
    line=line.strip()
    if line.startswith("mes/count "):
        try:
            if int(json.loads(line[10:]).get("count",0))>0: pub+=1
        except: pass
    elif line.startswith("mes/log/Line_A "):
        try: d=json.loads(line[15:])
        except: continue
        w=int(d.get("pulse_ms",0))
        if d.get("accepted"):
            acc+=1; print(f"  ✅#{acc}  폭 {w}ms", flush=True)
        else:
            rej+=1; print(f"  ✗ 거부 {w}ms", flush=True)
s1=dbsum()
print(f"\n=== 판정 (목표: 정확히 7) ===")
print(f"센서 채택: {acc}   거부(노이즈): {rej}")
print(f"MQTT 발행: {pub}")
print(f"DB 증가: {s1-s0}")
v = "✅ 정확" if acc==7 else (f"⚠️ {acc}개 — {'초과(노이즈 오카운트?)' if acc>7 else '부족(미감지)'}")
print(f"→ 센서 7개 인식: {v}")
print(f"→ 유실: {'✅ 없음' if (s1-s0)==pub==acc else f'발행{pub} DB{s1-s0} 채택{acc} 불일치'}")
