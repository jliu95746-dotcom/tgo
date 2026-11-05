"""Storage services module.

Provides a unified interface for file storage.
Supports local storage, Aliyun OSS and MinIO/S3.
"""

from app.core.config import settings
from app.services.storage.base import StorageBackend
from app.services.storage.local import LocalStorageBackend


def _required_storage_value(value: object, name: str) -> str:
    """Reject missing cloud settings without exposing configuration values."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing storage configuration: {name}")
    return value


def get_storage_backend() -> StorageBackend:
    """
    Factory function to get the configured storage backend.
    """
    storage_type = settings.STORAGE_TYPE.strip().lower()

    if storage_type == "oss":
        from app.services.storage.aliyun_oss import AliyunOSSBackend

        return AliyunOSSBackend(
            endpoint=_required_storage_value(
                settings.OSS_ENDPOINT, "OSS_ENDPOINT"
            ),
            bucket_name=_required_storage_value(
                settings.OSS_BUCKET_NAME, "OSS_BUCKET_NAME"
            ),
            access_key_id=_required_storage_value(
                settings.OSS_ACCESS_KEY_ID, "OSS_ACCESS_KEY_ID"
            ),
            access_key_secret=_required_storage_value(
                settings.OSS_ACCESS_KEY_SECRET, "OSS_ACCESS_KEY_SECRET"
            ),
            bucket_url=settings.OSS_BUCKET_URL,
        )
    elif storage_type == "minio":
        from app.services.storage.minio import MinIOBackend

        return MinIOBackend(
            endpoint_url=_required_storage_value(
                settings.MINIO_URL, "MINIO_URL"
            ),
            access_key_id=_required_storage_value(
                settings.MINIO_ACCESS_KEY_ID, "MINIO_ACCESS_KEY_ID"
            ),
            secret_access_key=_required_storage_value(
                settings.MINIO_SECRET_ACCESS_KEY, "MINIO_SECRET_ACCESS_KEY"
            ),
            bucket_name=_required_storage_value(
                settings.MINIO_BUCKET_NAME, "MINIO_BUCKET_NAME"
            ),
            upload_url=settings.MINIO_UPLOAD_URL,
            download_url=settings.MINIO_DOWNLOAD_URL,
        )

    if storage_type != "local":
        raise ValueError("Invalid STORAGE_TYPE; choose local, oss or minio")

    return LocalStorageBackend(
        base_path=getattr(settings, "UPLOAD_DIR", "./uploads"),
        api_base_url=settings.API_BASE_URL,
    )


# Global storage instance (lazy initialization)
_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """Get the global storage backend instance."""
    global _storage
    if _storage is None:
        _storage = get_storage_backend()
    return _storage


__all__ = [
    "StorageBackend",
    "LocalStorageBackend",
    "get_storage_backend",
    "get_storage",
]
