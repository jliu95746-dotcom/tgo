"""MinIO/S3 storage backend."""

import asyncio
from typing import BinaryIO, Any, Optional
from urllib.parse import urlparse

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    boto3 = None
    ClientError = Exception

from app.services.storage.base import StorageBackend
from app.services.storage.urls import resolve_private_chat_file_url
from app.core.config import settings


class MinIOBackend(StorageBackend):
    """MinIO storage implementation (S3-compatible)."""

    def __init__(
        self,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        upload_url: Optional[str] = None,
        download_url: Optional[str] = None,
        region_name: str = "us-east-1",
    ):
        """
        Initialize MinIO/S3 backend.

        Args:
            endpoint_url: S3 endpoint URL
            access_key_id: S3 access key ID
            secret_access_key: S3 secret access key
            bucket_name: S3 bucket name
            upload_url: Optional internal URL for uploads
            download_url: Optional public URL for downloads (CDN or custom domain)
            region_name: S3 region name (default us-east-1)
        """
        if boto3 is None:
            raise ImportError(
                "The 'boto3' package is required for MinIOBackend. "
                "Install it with 'pip install boto3'."
            )

        self.bucket_name = bucket_name
        self.download_url = (download_url or endpoint_url).rstrip("/")
        self.upload_url = (upload_url or endpoint_url).rstrip("/")
        
        # Initialize boto3 client
        self.s3 = boto3.client(
            "s3",
            endpoint_url=self.upload_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region_name,
        )

    async def read(self, path: str, *, max_bytes: int) -> bytes:
        def read_bounded() -> bytes:
            if max_bytes <= 0 or path.startswith("/") or ".." in path.split("/"):
                raise ValueError("Invalid storage read")
            response = self.s3.get_object(Bucket=self.bucket_name, Key=path)
            stream = response["Body"]
            try:
                if response.get("ContentLength", 0) > max_bytes:
                    raise ValueError("Storage object exceeds read limit")
                content = stream.read(max_bytes + 1)
                if not isinstance(content, bytes) or len(content) > max_bytes:
                    raise ValueError("Invalid storage object size")
                return content
            finally:
                stream.close()
        return await asyncio.to_thread(read_bounded)

    async def upload(self, file: BinaryIO, path: str, content_type: str) -> str:
        """Upload file to MinIO/S3."""
        loop = asyncio.get_event_loop()
        clean_path = path.lstrip("/")
        
        await loop.run_in_executor(
            None,
            lambda: self.s3.put_object(
                Bucket=self.bucket_name,
                Key=clean_path,
                Body=file,
                ContentType=content_type
            )
        )
        return self.get_public_url(path)

    async def delete(self, path: str) -> bool:
        """Delete file from MinIO/S3."""
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.s3.delete_object(
                    Bucket=self.bucket_name,
                    Key=path.lstrip("/")
                )
            )
            return True
        except Exception:
            return False

    def get_public_url(self, path: str) -> str:
        """
        Get public URL for a file path.
        """
        clean_path = path.lstrip("/")
        # If download_url is just a domain, we need to append bucket name if not using virtual-host style
        # For MinIO, usually it's http://domain:port/bucket/path
        if self.bucket_name not in self.download_url:
            return f"{self.download_url}/{self.bucket_name}/{clean_path}"
        return f"{self.download_url}/{clean_path}"

    async def exists(self, path: str) -> bool:
        """Check if file exists in MinIO/S3."""
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.s3.head_object(
                    Bucket=self.bucket_name,
                    Key=path.lstrip("/")
                )
            )
            return True
        except ClientError:
            return False
        except Exception:
            return False

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
