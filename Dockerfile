FROM python:3.11-slim

WORKDIR /app

# 시스템 패키지 (mysqlclient 빌드용 gcc + KST 타임존용 tzdata)
# tzdata 없으면 TZ=Asia/Seoul 환경변수가 무시되고 Python이 UTC로 폴백됨
# fonts-nanum: 증명서 PDF(cert_pdf.py)의 한글 출력용. 없으면 발급이 실패한다
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    tzdata \
    freetds-dev \
    fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
