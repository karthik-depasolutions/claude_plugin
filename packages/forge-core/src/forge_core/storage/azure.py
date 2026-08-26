"""Azure Blob Storage adapter for the ObjectStorage protocol."""

from __future__ import annotations

import asyncio
import logging
from typing import BinaryIO

from forge_core.storage.protocol import ObjectStorage

logger = logging.getLogger("forge_core.storage.azure")


class AzureBlobStorageAdapter:
    """Production object storage adapter for Azure Blob Storage."""

    def __init__(
        self,
        account_url: str | None = None,
        container_name: str = "data2plugin-artifacts",
        connection_string: str | None = None,
    ) -> None:
        self.account_url = account_url
        self.container_name = container_name
        self.connection_string = connection_string
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from azure.storage.blob import BlobServiceClient

            if self.connection_string:
                self._client = BlobServiceClient.from_connection_string(self.connection_string)
            elif self.account_url:
                from azure.identity import DefaultAzureCredential

                self._client = BlobServiceClient(account_url=self.account_url, credential=DefaultAzureCredential())
            else:
                raise ValueError("Neither account_url nor connection_string was provided for Azure Blob Storage.")
            return self._client
        except ImportError as exc:
            raise ImportError(
                "azure-storage-blob package is required to use AzureBlobStorageAdapter. "
                "Install it with: uv add azure-storage-blob azure-identity"
            ) from exc

    async def put(self, key: str, data: bytes | BinaryIO, content_type: str | None = None) -> str:
        client = self._get_client()
        blob_client = client.get_blob_client(container=self.container_name, blob=key)

        def _upload():
            if isinstance(data, bytes):
                blob_client.upload_blob(data, overwrite=True)
            else:
                data.seek(0)
                blob_client.upload_blob(data, overwrite=True)

        await asyncio.to_thread(_upload)
        return blob_client.url

    async def get(self, key: str) -> bytes:
        client = self._get_client()
        blob_client = client.get_blob_client(container=self.container_name, blob=key)

        def _download():
            stream = blob_client.download_blob()
            return stream.readall()

        return await asyncio.to_thread(_download)

    async def delete(self, key: str) -> bool:
        client = self._get_client()
        blob_client = client.get_blob_client(container=self.container_name, blob=key)

        def _del():
            if blob_client.exists():
                blob_client.delete_blob()
                return True
            return False

        return await asyncio.to_thread(_del)

    async def exists(self, key: str) -> bool:
        client = self._get_client()
        blob_client = client.get_blob_client(container=self.container_name, blob=key)
        return await asyncio.to_thread(blob_client.exists)

    async def presign(self, key: str, expires_in: int = 3600) -> str:
        client = self._get_client()
        blob_client = client.get_blob_client(container=self.container_name, blob=key)
        return blob_client.url
