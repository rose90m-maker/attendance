#!/usr/bin/env python3
"""_preview_serve.py — 대시보드 미리보기 사이트 (실데이터, 요청마다 갱신)

정적 파일이 아니라 요청할 때마다 운영 dashboard() 를 다시 호출하므로
새로고침하면 출근 인원·전력이 실제로 올라간다.

⚠️ 운영 앱을 그대로 띄우는 게 아니다. _preview_dump 가 import 전에
   MQTT 구독(client ID 충돌로 운영 MES 유실)·HAZMAT 모니터·외부 발송을 막는다.
   여기서는 그 위에 미리보기 라우트 하나만 얹은 별도 서버를 돌린다.
   DB 는 SELECT 만 한다.

사용:  .venv/bin/python3 _preview_serve.py [--port 8899] [--lan]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from _preview_dump import capture  # noqa: E402  (부작용 차단이 먼저 걸린다)
from _preview_render import sparkline  # noqa: E402
import app_maria as A  # noqa: E402

from flask import Flask, render_template  # noqa: E402

app = Flask(__name__, template_folder=os.path.join(HERE, "templates"))


@app.route("/")
def preview():
    ctx = capture()
    line, area = sparkline(ctx.get("power_info", {}).get("hourly_kwh", []))
    ctx["spark_line"], ctx["spark_area"] = line, area
    return render_template("_preview_dashboard.html", **ctx)


@app.route("/health")
def health():
    return {"ok": True}


def main():
    port = 8899
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    host = "0.0.0.0" if "--lan" in sys.argv else "127.0.0.1"
    print(f"\n  미리보기: http://localhost:{port}/")
    if host == "0.0.0.0":
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        print(f"  같은 망에서: http://{s.getsockname()[0]}:{port}/")
        s.close()
    print("  새로고침하면 실데이터가 다시 조회됩니다. 종료는 Ctrl+C.\n")
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
