#!/bin/bash
# start_tbm.sh — TBM 관리 앱 시작 스크립트 (로컬 / NAS 공용)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/tbm_app.log"
PID_FILE="$SCRIPT_DIR/tbm_app.pid"

start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "[TBM] 이미 실행 중 (PID: $(cat "$PID_FILE"))"
        exit 0
    fi
    echo "[TBM] 시작중..."
    cd "$SCRIPT_DIR" || exit 1
    # venv가 있으면 활성화
    if [ -f ".venv/bin/python3" ]; then
        PYTHON=".venv/bin/python3"
    elif [ -f "/opt/bin/python3" ]; then   # NAS
        PYTHON="/opt/bin/python3"
    else
        PYTHON="python3"
    fi
    nohup "$PYTHON" tbm_app.py >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "[TBM] 시작 완료 (PID: $!, port: 5051)"
    echo "[TBM] 로그: $LOG_FILE"
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            rm -f "$PID_FILE"
            echo "[TBM] 중지 완료 (PID: $PID)"
        else
            echo "[TBM] 프로세스 없음, PID 파일 삭제"
            rm -f "$PID_FILE"
        fi
    else
        echo "[TBM] 실행중인 프로세스 없음"
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "[TBM] 실행 중 (PID: $(cat "$PID_FILE"), port: 5051)"
    else
        echo "[TBM] 중지됨"
    fi
}

case "$1" in
    start)   start   ;;
    stop)    stop    ;;
    restart) stop; sleep 1; start ;;
    status)  status  ;;
    *)
        echo "사용법: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
