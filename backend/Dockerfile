FROM python:3.11-slim

WORKDIR /app

# System deps (if you need BLE here later, add bluez; for now backend is HTTP only)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend
COPY data /app/data
COPY config /app/config

ENV PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    SPOTIFY_CREDENTIALS_FILE=/app/config/spotify_credentials.txt

EXPOSE 5001
WORKDIR /app/backend

CMD ["python", "api_backend.py"]
