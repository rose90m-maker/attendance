FROM python:3.11-slim

WORKDIR /app

# 시스템 패키지 (mysqlclient 빌드용 gcc + KST 타임존용 tzdata)
# tzdata 없으면 TZ=Asia/Seoul 환경변수가 무시되고 Python이 UTC로 폴백됨
# fonts-nanum: 원천징수영수증 PDF 의 한글 렌더링용. 없으면 일본어/중국어 폰트로
#   대체 렌더링되어 표 열이 어긋난다 (2026-08-07 확인)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    tzdata \
    freetds-dev \
    fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 원천징수영수증 PDF 변환용 headless Chromium (wht_receipt.html_to_pdf)
# 없으면 PDF 대신 HTML 로 폴백된다
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 5050

CMD ["gunicorn", \
     "--workers", "4", \
     "--worker-class", "gthread", \
     "--threads", "2", \
     "--bind", "0.0.0.0:5050", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "--access-logfile", "-", \
     "app_maria:app"]
