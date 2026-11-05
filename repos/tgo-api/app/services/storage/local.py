"""Local file system storage backend."""

import asyncio
import os
import shutil
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse

from app.services.storage.base import StorageBackend


class LocalStorageBackend(StorageBackend):
    """Local file system storage implementation."""

    def __init__(self, base_path: str, api_base_url: str) -> None:
        """
        Initialize local storage backend.

        Args:
            base_path: Base directory for file storage
            api_base_url: Base URL for generating public URLs
        """
        self.base_path = Path(base_path)
        self.api_base_url = api_base_url.rstrip("/")
        self._ensure_base_path()

    def _ensure_base_path(self) -> None:
        """Ensure base storage directory exists."""
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _object_path(self, path: str) -> Path:
        """Resolve a relative object key without following storage symlinks."""
        parts = path.split("/")
        if (
            not path
            or "\\" in path
            or ":" in path
            or "\x00" in path
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("Invalid storage object path")
        root = self.base_path.resolve()
        target = root
        for part in parts:
            target = target / part
            if target.is_symlink():
                raise ValueError("Invalid storage object path")
        target = target.resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise ValueError("Invalid storage object path") from None
        return target

    async def read(self, path: str, *, max_bytes: int) -> bytes:
        """Reject path escapes and oversized stored objects."""

        def read_bounded() -> bytes:
            if max_bytes <= 0:
                raise ValueError("Invalid storage read")
            target = self._object_path(path)
            if not target.is_file() or target.stat().st_size > max_bytes:
                raise ValueError("Invalid storage object size")
            with target.open("rb") as stream:
                content = stream.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise ValueError("Storage object exceeds read limit")
            return content

        return await asyncio.to_thread(read_bounded)

    async def upload(
        self, file: BinaryIO, path: str, content_type: str
    ) -> str:
        """Upload file to local storage."""
        full_path = self._object_path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # Write file synchronously (file uploads are already buffered)
        with open(full_path, "wb") as f:
            shutil.copyfileobj(file, f)

        return self.get_public_url(path)

    async def delete(self, path: str) -> bool:
        """Delete file from local storage."""
        try:
            full_path = self._object_path(path)
            if full_path.is_file():
                os.remove(full_path)
                return True
            return False
        except Exception:
            return False

    def get_public_url(self, path: str) -> str:
        """
        Get public URL for a file path.

        Handles both:
        - API paths: /v1/chat/files/{id}
        - Storage paths: chat/{project}/{id}/file.png
        """
        if path.startswith("/"):
            # Already an API path
            return f"{self.api_base_url}{path}"
        # Storage path - convert to API path
        return f"{self.api_base_url}/v1/chat/files/{path}"

    async def exists(self, path: str) -> bool:
        """Check if file exists in local storage."""
        try:
            return self._object_path(path).is_file()
        except (OSError, ValueError):
            return False

    def resolve_url(self, url: str) -> str:
        """
        Resolve and normalize a possibly relative or incorrect public URL.
        """
        if not url or not isinstance(url, str):
            return url

        # Strip /api from relative URLs for internal storage resolution.
        path = url
        if path.startswith("/api/v1"):
            path = path[4:]

        if path.startswith("/"):
            # Relative path: /v1/chat/files/xxx
            return self.get_public_url(path)

        if url.startswith("http://") or url.startswith("https://"):
            # Full URL: Resolve ONLY if it points to localhost/127.0.0.1
            parsed = urlparse(url)
            if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
                p = parsed.path
                if p.startswith("/api/v1"):
                    p = p[4:]
                if parsed.query:
                    p += f"?{parsed.query}"
                return self.get_public_url(p)

        return url

    def get_file_access_url(self, file_id: str, storage_url: str) -> str:
        """
        For local storage, return API endpoint URL.
        Frontend accesses files via /v1/chat/files/{file_id}
        """
        return f"{self.api_base_url}/v1/chat/files/{file_id}"
