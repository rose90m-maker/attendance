#!/bin/bash
# RAG 서버 watchdog — 서버가 죽으면 5초 후 자동 재시작
VENV="/Users/changkooji/attendance/.venv/bin/python"
SCRIPT="/Users/changkooji/attendance/rag_server.py"
LOG="/Users/changkooji/attendance/rag_server.log"

while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] rag_server 시작" | tee -a "$LOG"
    "$VENV" "$SCRIPT" >> "$LOG" 2>&1
    EXIT_CODE=$?
    # SIGTERM(143) = 정상 종료 요청 → 재시작 안 함
    if [ $EXIT_CODE -eq 143 ] || [ $EXIT_CODE -eq 130 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] 정상 종료 (exit $EXIT_CODE) → 중지" | tee -a "$LOG"
        break
    fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] 비정상 종료 (exit $EXIT_CODE) → 5초 후 재시작" | tee -a "$LOG"
    sleep 5
done
