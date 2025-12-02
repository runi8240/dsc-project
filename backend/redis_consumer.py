import threading
import time
from typing import Callable, Dict, Optional

import redis


class RedisStreamConsumer:
    """Consume telemetry messages from a Redis Stream using a consumer group."""

    def __init__(
        self,
        redis_url: str,
        stream_key: str = "telemetry",
        group: str = "backend",
        consumer_name: Optional[str] = None,
        handler: Optional[Callable[[Dict], None]] = None,
    ):
        self.redis_url = redis_url
        self.stream_key = stream_key
        self.group = group
        self.consumer_name = consumer_name or f"consumer-{int(time.time())}"
        self.handler = handler
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.client = redis.Redis.from_url(self.redis_url)

    def start(self):
        self._ensure_group()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _ensure_group(self):
        try:
            self.client.xgroup_create(name=self.stream_key, groupname=self.group, id="0-0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            # Group already exists
            if "BUSYGROUP" not in str(exc):
                raise

    def _loop(self):
        while not self._stop.is_set():
            try:
                messages = self.client.xreadgroup(
                    groupname=self.group,
                    consumername=self.consumer_name,
                    streams={self.stream_key: ">"},
                    count=10,
                    block=2000,  # 2s
                )
            except Exception as exc:
                print(f"Redis consumer error: {exc}")
                time.sleep(1)
                continue

            for _stream, items in messages or []:
                for msg_id, data in items:
                    try:
                        if self.handler:
                            self.handler(data)
                        self.client.xack(self.stream_key, self.group, msg_id)
                    except Exception as exc:
                        print(f"Failed to handle message {msg_id}: {exc}")
