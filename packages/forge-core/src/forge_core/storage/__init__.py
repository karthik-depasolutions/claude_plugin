"""Object storage abstraction layer for Data2plugin."""

from __future__ import annotations

import os
from typing import Literal

from forge_core.storage.azure import AzureBlobStorageAdapter
from forge_core.storage.local import LocalStorageAdapter
from forge_core.storage.protocol import ObjectStorage


def get_storage(
    provider: Literal["local", "azure"] | str | None = None,
    base_dir: str | None = None,
    **kwargs,
) -> ObjectStorage:
    """Factory creating an ObjectStorage adapter based on environment or explicit config."""
    chosen_provider = provider or os.getenv("STORAGE_PROVIDER", "local").lower()

    if chosen_provider == "azure":
        account_url = kwargs.get("account_url") or os.getenv("AZURE_STORAGE_ACCOUNT_URL")
        container = kwargs.get("container") or os.getenv("AZURE_STORAGE_CONTAINER", "data2plugin-artifacts")
        conn_str = kwargs.get("connection_string") or os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        return AzureBlobStorageAdapter(
            account_url=account_url,
            container_name=container,
            connection_string=conn_str,
        )

    # Default to LocalStorageAdapter
    storage_dir = base_dir or os.getenv("STORAGE_DIR", "./storage")
    return LocalStorageAdapter(base_dir=storage_dir)


__all__ = [
    "AzureBlobStorageAdapter",
    "LocalStorageAdapter",
    "ObjectStorage",
    "get_storage",
]
