FROM python:3.11-slim

WORKDIR /app

# BLE requirements
RUN apt-get update && apt-get install -y --no-install-recommends \
    bluez \
    libglib2.0-0 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ingestion /app/ingestion

ENV PYTHONUNBUFFERED=1 \
    GARMIN_DEVICE_ID=F3FD4758-51E9-1BD3-36D9-80DF3F6C6B79 \
    BACKEND_TELEMETRY_URL=http://backend:5001/telemetry

WORKDIR /app/ingestion

CMD ["python", "ingestion_service.py"]
