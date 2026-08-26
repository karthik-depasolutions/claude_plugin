"""Unit tests for the ObjectStorage abstraction and adapters."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from forge_core.storage import (
    LocalStorageAdapter,
    ObjectStorage,
    get_storage,
)


@pytest.mark.asyncio
async def test_local_storage_adapter_crud(tmp_path: Path):
    storage = LocalStorageAdapter(base_dir=tmp_path)
    assert isinstance(storage, ObjectStorage)

    # 1. Put bytes
    key = "tenant_1/datasets/sample.csv"
    data = b"id,name\n1,Alice\n2,Bob"
    uri = await storage.put(key, data)
    assert uri.startswith("file://")

    # 2. Exists
    assert await storage.exists(key) is True
    assert await storage.exists("nonexistent.txt") is False

    # 3. Get bytes
    retrieved = await storage.get(key)
    assert retrieved == data

    # 4. Put stream (BinaryIO)
    stream_key = "tenant_1/datasets/stream.csv"
    stream_data = io.BytesIO(b"id,val\n10,100")
    await storage.put(stream_key, stream_data)
    assert await storage.get(stream_key) == b"id,val\n10,100"

    # 5. Presign
    presigned = await storage.presign(key)
    assert "sample.csv" in presigned

    # 6. Delete
    deleted = await storage.delete(key)
    assert deleted is True
    assert await storage.exists(key) is False
    assert await storage.delete("nonexistent.txt") is False


@pytest.mark.asyncio
async def test_local_storage_path_traversal_guard(tmp_path: Path):
    storage = LocalStorageAdapter(base_dir=tmp_path)
    with pytest.raises(ValueError, match="Path traversal detected"):
        await storage.put("../../../evil.txt", b"evil")


def test_get_storage_factory(tmp_path: Path):
    storage = get_storage("local", base_dir=str(tmp_path))
    assert isinstance(storage, LocalStorageAdapter)
    assert storage.base_dir == tmp_path
