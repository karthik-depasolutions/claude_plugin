"""Object Storage protocol specification for Data2plugin artifact persistence."""

from __future__ import annotations

from typing import BinaryIO, Protocol, runtime_checkable


@runtime_checkable
class ObjectStorage(Protocol):
    """Protocol for durable object storage (local filesystem, Azure Blob, S3)."""

    async def put(self, key: str, data: bytes | BinaryIO, content_type: str | None = None) -> str:
        """Stores an object at key and returns its URI or storage identifier."""
        ...

    async def get(self, key: str) -> bytes:
        """Retrieves raw byte contents of an object."""
        ...

    async def delete(self, key: str) -> bool:
        """Deletes an object if it exists. Returns True if deleted."""
        ...

    async def exists(self, key: str) -> bool:
        """Checks if an object exists at key."""
        ...

    async def presign(self, key: str, expires_in: int = 3600) -> str:
        """Generates a temporary read URL or access URI."""
        ...
