FROM python:3.11-slim

WORKDIR /app

# 시스템 패키지 (필요시 mysqlclient 빌드용)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5050

CMD ["gunicorn", "--workers", "4", "--bind", "0.0.0.0:5050", "--timeout", "120", "--access-logfile", "-", "app_maria:app"]
