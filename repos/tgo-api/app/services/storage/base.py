"""Storage backend abstraction for file storage."""

from abc import ABC, abstractmethod
from typing import BinaryIO


class StorageBackend(ABC):
    """Abstract base class for file storage backends."""

    async def read(self, path: str, *, max_bytes: int) -> bytes:
        """Read a server-owned object key with a strict byte limit."""
        raise NotImplementedError(
            "This storage backend does not support bounded reads"
        )

    @abstractmethod
    async def upload(
        self, file: BinaryIO, path: str, content_type: str
    ) -> str:
        (
            "\n"
            "        Upload a file to storage.\n"
            "        \n"
            "        Args:\n"
            "            file: File-like object to upload\n"
            "            path: Relative path to store the file\n"
            "            content_type: MIME type of the file\n"
            "            \n"
            "        Returns:\n"
            "            Public URL to access the file\n"
            "        "
        )
        pass

    @abstractmethod
    async def delete(self, path: str) -> bool:
        (
            "\n"
            "        Delete a file from storage.\n"
            "        \n"
            "        Args:\n"
            "            path: Relative path of the file to delete\n"
            "            \n"
            "        Returns:\n"
            "            True if deleted successfully\n"
            "        "
        )
        pass

    @abstractmethod
    def get_public_url(self, path: str) -> str:
        (
            "\n"
            "        Get the public URL for a file path.\n"
            "        \n"
            "        Args:\n"
            "            path: Relative path or API path (e.g., "
            "/v1/chat/files/{id})\n"
            "            \n"
            "        Returns:\n"
            "            Full public URL to access the file\n"
            "        "
        )
        pass

    @abstractmethod
    async def exists(self, path: str) -> bool:
        (
            "\n"
            "        Check if a file exists.\n"
            "        \n"
            "        Args:\n"
            "            path: Relative path of the file\n"
            "            \n"
            "        Returns:\n"
            "            True if file exists\n"
            "        "
        )
        pass

    @abstractmethod
    def resolve_url(self, url: str) -> str:
        (
            "\n"
            "        Resolve and normalize a possibly relative or incorrect "
            "public URL.\n"
            "        \n"
            "        Args:\n"
            "            url: The URL to resolve (relative path or absolute "
            "URL)\n"
            "            \n"
            "        Returns:\n"
            "            A normalized absolute public URL\n"
            "        "
        )
        pass

    @abstractmethod
    def get_file_access_url(self, file_id: str, storage_url: str) -> str:
        (
            "\n"
            "        Get the URL that frontend should use to access the "
            "file.\n"
            "        \n"
            "        For local storage: returns API endpoint URL "
            "(/v1/chat/files/{file_id})\n"
            "        For cloud storage: returns the direct storage URL\n"
            "        \n"
            "        Args:\n"
            "            file_id: The database file ID\n"
            "            storage_url: The URL returned by upload() method\n"
            "            \n"
            "        Returns:\n"
            "            The URL that frontend should use to access the file\n"
            "        "
        )
        pass
