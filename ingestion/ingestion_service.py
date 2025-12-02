import asyncio
import os
import threading
import time
from collections import deque
from typing import Dict, Optional

import requests
from bleak import BleakClient
import redis

# BLE constants
HR_CHAR = "00002a37-0000-1000-8000-00805f9b34fb"
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

# Simple queue for telemetry so BLE handler stays fast
telemetry_queue: deque[Dict[str, float]] = deque()
stop_event = threading.Event()


def enqueue_telemetry(hr: int) -> None:
    telemetry_queue.append({"hr": hr, "timestamp": time.time()})


def telemetry_worker() -> None:
    while not stop_event.is_set():
        try:
            payload = telemetry_queue.popleft()
        except IndexError:
            stop_event.wait(0.05)
            continue

        try:
            if redis_client:
                redis_client.xadd(REDIS_STREAM_KEY, {"hr": payload["hr"], "timestamp": payload["timestamp"]})
            else:
                requests.post(BACKEND_TELEMETRY_URL, json=payload, timeout=POST_TIMEOUT)
        except Exception as exc:
            # Keep BLE loop running even if backend is temporarily unavailable
            print(f"Failed to POST telemetry: {exc}")


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


if __name__ == "__main__":
    main()
