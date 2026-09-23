"""Aliyun OSS storage backend."""

import asyncio
from typing import BinaryIO, Any
from urllib.parse import urlparse

try:
    import oss2
except ImportError:
    oss2 = None

from app.services.storage.base import StorageBackend
from app.services.storage.urls import resolve_private_chat_file_url
from app.core.config import settings


class AliyunOSSBackend(StorageBackend):
    """Aliyun OSS storage implementation."""

    def __init__(
        self,
        endpoint: str,
        bucket_name: str,
        access_key_id: str,
        access_key_secret: str,
        bucket_url: str = None,
    ):
        """
        Initialize Aliyun OSS backend.

        Args:
            endpoint: OSS endpoint
            bucket_name: OSS bucket name
            access_key_id: OSS access key ID
            access_key_secret: OSS access key secret
            bucket_url: Base URL for generating public URLs (can be a custom domain)
        """
        if oss2 is None:
            raise ImportError(
                "The 'oss2' package is required for AliyunOSSBackend. "
                "Install it with 'pip install oss2'."
            )

        self.auth = oss2.Auth(access_key_id, access_key_secret)
        self.bucket = oss2.Bucket(self.auth, endpoint, bucket_name)
        
        # bucket_url should be something like https://bucket-name.oss-cn-hangzhou.aliyuncs.com
        # or a custom domain like https://cdn.example.com
        self.bucket_url = (bucket_url or f"https://{bucket_name}.{endpoint}").rstrip("/")

    async def read(self, path: str, *, max_bytes: int) -> bytes:
        def read_bounded() -> bytes:
            if max_bytes <= 0 or path.startswith("/") or ".." in path.split("/"):
                raise ValueError("Invalid storage read")
            stream = self.bucket.get_object(path)
            try:
                if stream.content_length is not None and stream.content_length > max_bytes:
                    raise ValueError("Storage object exceeds read limit")
                content = stream.read(max_bytes + 1)
                if not isinstance(content, bytes) or len(content) > max_bytes:
                    raise ValueError("Invalid storage object size")
                return content
            finally:
                stream.close()
        return await asyncio.to_thread(read_bounded)

    async def upload(self, file: BinaryIO, path: str, content_type: str) -> str:
        """Upload file to Aliyun OSS."""
        # Use run_in_executor to avoid blocking the event loop with synchronous oss2 SDK
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, 
            lambda: self.bucket.put_object(path.lstrip("/"), file, headers={"Content-Type": content_type})
        )
        return self.get_public_url(path)

    async def delete(self, path: str) -> bool:
        """Delete file from Aliyun OSS."""
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.bucket.delete_object(path.lstrip("/"))
            )
            return True
        except Exception:
            return False

    def get_public_url(self, path: str) -> str:
        """
        Get public URL for a file path.
        """
        clean_path = path.lstrip("/")
        # If it's an API path (/v1/chat/files/...), we assume it should be handled 
        # normally, but usually OSS backends return direct links.
        if clean_path.startswith("v1/chat/files/"):
            # If the application is configured to serve files via API even with OSS, 
            # we might need different logic. But usually, OSS means direct CDN access.
            return f"{self.bucket_url}/{clean_path}"
            
        return f"{self.bucket_url}/{clean_path}"

    async def exists(self, path: str) -> bool:
        """Check if file exists in Aliyun OSS."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.bucket.object_exists(path.lstrip("/"))
        )

    def resolve_url(self, url: str) -> str:
        """
        Resolve and normalize a possibly relative or incorrect public URL.
        """
        if not url or not isinstance(url, str):
            return url

        private_url = resolve_private_chat_file_url(url, settings.API_BASE_URL)
        if private_url is not None:
            return private_url
            
        # If it's a relative path, resolve it using get_public_url
        if url.startswith("/"):
            # Strip /api/v1 if present
            path = url
            if path.startswith("/api/v1"):
                path = path[4:]
            return self.get_public_url(path)
            
        # If it's an absolute URL, check if it's localhost and fix it
        if url.startswith("http://") or url.startswith("https://"):
            parsed = urlparse(url)
            if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
                p = parsed.path
                if p.startswith("/api/v1"):
                    p = p[4:]
                if parsed.query:
                    p += f"?{parsed.query}"
                if parsed.fragment:
                    p += f"#{parsed.fragment}"
                return self.get_public_url(p)
                
        return url

    def get_file_access_url(self, file_id: str, storage_url: str) -> str:
        """
        For cloud storage, return the direct storage URL.
        """
        return storage_url
