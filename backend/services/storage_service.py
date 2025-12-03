import io
import json
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from minio import Minio
from minio.error import S3Error


class StorageClient:
    """Lightweight wrapper around MinIO/S3-compatible storage."""

    def __init__(
        self,
        enabled: bool,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
        base_path: Optional[Path] = None,
    ):
        self.enabled = enabled
        self.bucket = bucket
        self.base_path = base_path or Path("/tmp")
        self._client: Optional[Minio] = None
        if not self.enabled:
            return

        endpoint_host = endpoint
        if "://" in endpoint:
            parsed = urlparse(endpoint)
            endpoint_host = parsed.netloc or parsed.path
        if not endpoint_host:
            raise ValueError("Storage endpoint must not be empty")

        try:
            self._client = Minio(
                endpoint_host,
                access_key=access_key,
                secret_key=secret_key,
                secure=secure,
            )
            self._ensure_bucket()
        except Exception as exc:
            # Disable storage but keep app running.
            print(f"[storage] failed to initialize: {exc}")
            self.enabled = False

    def _ensure_bucket(self) -> None:
        if not self.enabled or not self._client:
            return
        try:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)
        except S3Error as exc:
            print(f"[storage] bucket check failed: {exc}")
            raise

    def upload_bytes(self, key: str, data: bytes, content_type: str | None = None) -> bool:
        if not self.enabled or not self._client:
            return False
        stream = io.BytesIO(data)
        length = len(data)
        try:
            self._client.put_object(
                self.bucket,
                key,
                stream,
                length,
                content_type=content_type or "application/octet-stream",
            )
            return True
        except S3Error as exc:
            print(f"[storage] upload_bytes failed for {key}: {exc}")
            return False

    def upload_json(self, key: str, payload: Any) -> bool:
        return self.upload_bytes(key, json.dumps(payload).encode("utf-8"), "application/json")

    def upload_file(self, file_path: Path, key: str, content_type: str | None = None) -> bool:
        if not self.enabled or not self._client or not file_path.exists():
            return False
        try:
            file_size = file_path.stat().st_size
            with file_path.open("rb") as fh:
                self._client.put_object(
                    self.bucket,
                    key,
                    fh,
                    file_size,
                    content_type=content_type or _guess_content_type(file_path),
                )
            return True
        except (OSError, S3Error) as exc:
            print(f"[storage] upload_file failed for {key}: {exc}")
            return False

    def download_file(self, key: str, destination: Path) -> bool:
        if not self.enabled or not self._client:
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            response = self._client.get_object(self.bucket, key)
            try:
                with destination.open("wb") as fh:
                    for chunk in response.stream(32 * 1024):
                        fh.write(chunk)
            finally:
                response.close()
                response.release_conn()
            return True
        except (OSError, S3Error) as exc:
            print(f"[storage] download_file failed for {key}: {exc}")
            return False

    def object_exists(self, key: str) -> bool:
        if not self.enabled or not self._client:
            return False
        try:
            self._client.stat_object(self.bucket, key)
            return True
        except S3Error:
            return False


def _guess_content_type(file_path: Path) -> str:
    _, ext = os.path.splitext(file_path.name)
    if ext == ".csv":
        return "text/csv"
    if ext in {".json", ".jsonl"}:
        return "application/json"
    return "application/octet-stream"
