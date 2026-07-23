from __future__ import annotations
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env.

    Uses Pydantic Settings 2.x. All fields are validated and typed.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # tgo-api base URL
    api_base_url: str

    # PostgreSQL DSN for SQLAlchemy async engine
    database_url: str  # e.g. postgresql+asyncpg://user:pass@host:5432/db

    # SSE and HTTP behavior
    sse_backpressure_limit: int = 1000
    request_timeout_seconds: int = 120

    # Redis (optional) for caching
    redis_url: str | None = None  # e.g. redis://127.0.0.1:6379/0
    visitor_cache_ttl_seconds: int = 24 * 60 * 60

    # Inbound media ingestion (disabled until an encryption key is configured)
    media_ingestion_enabled: bool = False
    media_cleanup_enabled: bool = True
    media_storage_path: str = Field(default="./data/media", min_length=1, max_length=1024)
    media_encryption_key: SecretStr | None = None
    media_encryption_key_id: str = Field(
        default="local-media-v1",
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    media_retention_days: int = Field(default=30, ge=1, le=3650)
    media_download_timeout_seconds: int = Field(default=30, ge=1, le=600)
    media_image_max_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1024,
        le=100 * 1024 * 1024,
    )
    media_voice_max_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1024,
        le=100 * 1024 * 1024,
    )
    media_image_max_pixels: int = Field(default=25_000_000, ge=1, le=100_000_000)
    media_image_max_frames: int = Field(default=20, ge=1, le=1000)
    media_voice_max_duration_seconds: int = Field(default=300, ge=1, le=3600)
    media_job_batch_size: int = Field(default=5, ge=1, le=100)
    media_job_max_concurrency: int = Field(default=1, ge=1, le=16)
    media_job_max_attempts: int = Field(default=3, ge=1, le=20)
    media_job_lease_seconds: int = Field(default=120, ge=10, le=3600)
    media_job_total_timeout_seconds: int = Field(default=60, ge=1, le=900)
    media_job_poll_interval_seconds: int = Field(default=3, ge=1, le=60)
    media_cleanup_interval_seconds: int = Field(default=300, ge=10, le=86400)
    media_cleanup_batch_size: int = Field(default=20, ge=1, le=1000)

    # Logging
    log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL

    # Vision Agent service URL (for UI automation platforms like wechat_personal)
    vision_agent_url: str = "http://tgo-vision-agent:8000"


settings = Settings()

