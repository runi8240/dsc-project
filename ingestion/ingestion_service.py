import asyncio
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bleak import BleakClient
import redis

try:
    from backend.services.storage_service import StorageClient
except ModuleNotFoundError:
    import sys

    ROOT_DIR = Path(__file__).resolve().parents[1]
    sys.path.append(str(ROOT_DIR))
    from backend.services.storage_service import StorageClient

# BLE constants
HR_CHAR = "00002a37-0000-1000-8000-00805f9b34fb"
# GARMIN_ID = os.getenv(
#     "GARMIN_DEVICE_ID",
#     "1A4EDA26-AA5E-0D73-27F1-211B33814D3C",
# )

GARMIN_ID = os.getenv(
    "GARMIN_DEVICE_ID",
    "F3FD4758-51E9-1BD3-36D9-80DF3F6C6B79",
)


# Backend endpoint
BACKEND_TELEMETRY_URL = os.getenv("BACKEND_TELEMETRY_URL", "http://localhost:5001/telemetry")
POST_TIMEOUT = 4
REDIS_URL = os.getenv("REDIS_URL")
REDIS_STREAM_KEY = os.getenv("REDIS_STREAM_KEY", "telemetry")
redis_client: Optional[redis.Redis] = redis.Redis.from_url(REDIS_URL) if REDIS_URL else None
STORAGE_ENABLED = os.getenv("STORAGE_ENABLED", "false").lower() == "true"
STORAGE_ENDPOINT = os.getenv("STORAGE_ENDPOINT", "http://localhost:9000")
STORAGE_ACCESS_KEY = os.getenv("STORAGE_ACCESS_KEY", "minio")
STORAGE_SECRET_KEY = os.getenv("STORAGE_SECRET_KEY", "minio123")
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "dsc-artifacts")
STORAGE_SECURE = os.getenv("STORAGE_SECURE", "false").lower() == "true"
STORAGE_TELEMETRY_PREFIX = os.getenv("STORAGE_TELEMETRY_PREFIX", "raw-telemetry")
STORAGE_BATCH_SIZE = int(os.getenv("STORAGE_BATCH_SIZE", "25"))
STORAGE_FLUSH_SECONDS = float(os.getenv("STORAGE_FLUSH_SECONDS", "10"))
INGESTION_SESSION_ID = os.getenv("INGESTION_SESSION_ID", f"session-{int(time.time())}")
storage_client = StorageClient(
    enabled=STORAGE_ENABLED,
    endpoint=STORAGE_ENDPOINT,
    access_key=STORAGE_ACCESS_KEY,
    secret_key=STORAGE_SECRET_KEY,
    bucket=STORAGE_BUCKET,
    secure=STORAGE_SECURE,
)
storage_buffer: List[Dict[str, float]] = []
last_storage_flush = 0.0

# Simple queue for telemetry so BLE handler stays fast
telemetry_queue: deque[Dict[str, float]] = deque()
stop_event = threading.Event()


def _buffer_storage_payload(payload: Dict[str, float]) -> None:
    if not storage_client.enabled:
        return
    storage_buffer.append(payload)
    _flush_storage_buffer()


def _flush_storage_buffer(force: bool = False) -> None:
    global storage_buffer, last_storage_flush
    if not storage_client.enabled or not storage_buffer:
        return
    now = time.time()
    if not force:
        if len(storage_buffer) < STORAGE_BATCH_SIZE and now - last_storage_flush < STORAGE_FLUSH_SECONDS:
            return
    batch = list(storage_buffer)
    storage_buffer = []
    key = f"{STORAGE_TELEMETRY_PREFIX}/{INGESTION_SESSION_ID}/{int(now * 1000)}.json"
    success = storage_client.upload_json(
        key,
        {
            "session": INGESTION_SESSION_ID,
            "records": batch,
        },
    )
    if not success:
        # Re-queue for another attempt later
        storage_buffer = batch + storage_buffer
    else:
        last_storage_flush = now


def enqueue_telemetry(hr: int) -> None:
    telemetry_queue.append({"hr": hr, "timestamp": time.time()})


def telemetry_worker() -> None:
    global last_storage_flush
    while not stop_event.is_set():
        try:
            payload = telemetry_queue.popleft()
        except IndexError:
            stop_event.wait(0.05)
            continue

        try:
            if redis_client:
                redis_client.xadd(
                    REDIS_STREAM_KEY,
                    {
                        "hr": payload["hr"],
                        "timestamp": payload["timestamp"],
                    },
                )
            else:
                requests.post(BACKEND_TELEMETRY_URL, json=payload, timeout=POST_TIMEOUT)
            _buffer_storage_payload(payload)
        except Exception as exc:
            # Keep BLE loop running even if backend is temporarily unavailable
            print(f"Failed to POST telemetry: {exc}")
    _flush_storage_buffer(force=True)


def hr_handler(_sender, data: bytearray):
    if len(data) < 2:
        return
    hr = data[1]
    print(f"HR: {hr}")
    enqueue_telemetry(hr)


async def stream_hr():
    print("Connecting to Garmin…")
    async with BleakClient(GARMIN_ID) as client:
        print("Connected to Garmin")
        await client.start_notify(HR_CHAR, hr_handler)

        while not stop_event.is_set():
            await asyncio.sleep(0.1)


def run_ble():
    asyncio.run(stream_hr())


def main():
    worker = threading.Thread(target=telemetry_worker, daemon=True)
    worker.start()

    try:
        run_ble()
    finally:
        stop_event.set()
        worker.join(timeout=1)
        _flush_storage_buffer(force=True)


if __name__ == "__main__":
    main()
