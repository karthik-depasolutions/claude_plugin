"""Local filesystem implementation of the ObjectStorage protocol."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import BinaryIO


class LocalStorageAdapter:
    """Stores uploaded datasets and generated plugin packages on local disk."""

    def __init__(self, base_dir: str | Path = "./storage") -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, key: str) -> Path:
        # Sanitize key and prevent directory traversal
        clean_key = Path(key).as_posix().lstrip("/")
        full_path = (self.base_dir / clean_key).resolve()
        if not full_path.is_relative_to(self.base_dir):
            raise ValueError(f"Path traversal detected for key {key!r}")
        return full_path

    async def put(self, key: str, data: bytes | BinaryIO, content_type: str | None = None) -> str:
        target = self._resolve_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(data, bytes):
            await asyncio.to_thread(target.write_bytes, data)
        else:
            def _write_stream():
                with open(target, "wb") as f:
                    data.seek(0)
                    f.write(data.read())
            await asyncio.to_thread(_write_stream)

        return target.as_uri()

    async def get(self, key: str) -> bytes:
        target = self._resolve_path(key)
        if not target.exists():
            raise FileNotFoundError(f"Object not found: {key}")
        return await asyncio.to_thread(target.read_bytes)

    async def delete(self, key: str) -> bool:
        target = self._resolve_path(key)
        if not target.exists():
            return False
        await asyncio.to_thread(target.unlink)
        return True

    async def exists(self, key: str) -> bool:
        target = self._resolve_path(key)
        return await asyncio.to_thread(target.exists)

    async def presign(self, key: str, expires_in: int = 3600) -> str:
        target = self._resolve_path(key)
        return target.as_uri()
