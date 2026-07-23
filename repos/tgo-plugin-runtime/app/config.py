"""Application configuration using Pydantic Settings."""

from typing import Literal, Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Service Info
    SERVICE_NAME: str = Field(default="TGO Plugin Runtime")
    SERVICE_VERSION: str = Field(default="1.0.0")

    # Server Configuration
    HOST: str = Field(default="0.0.0.0")
    PORT: int = Field(default=8090)

    # Plugin Socket Configuration
    PLUGIN_SOCKET_PATH: str = Field(
        default="/var/run/tgo/tgo.sock",
        description="Unix socket path for plugin communication",
    )
    PLUGIN_TCP_PORT: Optional[int] = Field(
        default=None,
        description="TCP port for plugin communication (alternative to Unix socket)",
    )
    PLUGIN_REQUEST_TIMEOUT: int = Field(
        default=30,
        description="Timeout in seconds for plugin requests",
    )
    PLUGIN_PING_INTERVAL: int = Field(
        default=30,
        description="Interval in seconds for plugin heartbeat ping",
    )

    # AI Service Configuration (for tool sync)
    AI_SERVICE_URL: str = Field(
        default="http://localhost:8081",
        description="URL of the TGO AI service for tool sync",
    )
    AI_SERVICE_TIMEOUT: int = Field(
        default=30,
        description="Timeout for AI service requests in seconds",
    )

    # Database Configuration
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/tgo",
        description="PostgreSQL database URL",
    )
    DATABASE_POOL_SIZE: int = Field(default=5)
    DATABASE_MAX_OVERFLOW: int = Field(default=10)

    # Security
    SECRET_KEY: str = Field(
        default="secret-key-at-least-32-chars-long!!",
        description="Secret key for JWT verification (must match tgo-api)",
    )
    INTERNAL_API_KEY: Optional[str] = Field(
        default=None,
        description="Shared key for internal tgo-api calls",
    )
    BUSINESS_QUERY_MODE: Literal["disabled", "demo", "http"] = Field(
        default="disabled",
        description="Read-only business provider mode: disabled, demo, or http",
    )
    BUSINESS_QUERY_TIMEOUT: float = Field(default=5.0, gt=0)
    BUSINESS_API_BASE_URL: Optional[str] = None
    BUSINESS_API_ORDER_PATH: str = "/v1/orders/query"
    BUSINESS_API_LOGISTICS_PATH: str = "/v1/logistics/query"
    BUSINESS_API_METHOD: Literal["GET", "POST"] = "POST"
    BUSINESS_API_AUTH_MODE: Literal[
        "none", "bearer", "api_key", "basic"
    ] = "none"
    BUSINESS_API_AUTH_TOKEN: Optional[SecretStr] = None
    BUSINESS_API_KEY_HEADER: str = "X-API-Key"
    BUSINESS_API_BASIC_USERNAME: Optional[str] = None
    BUSINESS_API_BASIC_PASSWORD: Optional[SecretStr] = None
    BUSINESS_API_DATA_PATH: str = "data"
    BUSINESS_API_SUCCESS_FIELD: Optional[str] = None
    BUSINESS_API_SUCCESS_VALUE: str = "0"
    BUSINESS_API_TENANT_ID_FIELD: str = "tenant_id"
    BUSINESS_API_CUSTOMER_ID_FIELD: str = "customer_id"
    BUSINESS_API_ORDER_NO_FIELD: str = "order_no"
    BUSINESS_API_ORDER_STATUS_FIELD: str = "status"
    BUSINESS_API_ORDER_AMOUNT_FIELD: str = "amount_minor"
    BUSINESS_API_ORDER_AMOUNT_UNIT: Literal["minor", "major"] = "minor"
    BUSINESS_API_ORDER_CURRENCY_FIELD: str = "currency"
    BUSINESS_API_ORDER_CREATED_AT_FIELD: str = "created_at"
    BUSINESS_API_LOGISTICS_STATUS_FIELD: str = "status"
    BUSINESS_API_LOGISTICS_CARRIER_FIELD: str = "carrier"
    BUSINESS_API_LOGISTICS_TRACKING_NO_FIELD: str = "tracking_no"
    BUSINESS_API_LOGISTICS_UPDATED_AT_FIELD: str = "updated_at"

    # Plugin Storage
    PLUGIN_BASE_PATH: str = Field(
        default="/var/lib/tgo/plugins",
        description="Base directory for plugin storage",
    )

    # Logging
    LOG_LEVEL: str = Field(default="INFO")

    # Environment
    ENVIRONMENT: str = Field(default="development")
    DEBUG: bool = Field(default=False)

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT.lower() in ("development", "dev", "local")

    @property
    def database_url_sync(self) -> str:
        """Get synchronous database URL."""
        url = str(self.DATABASE_URL)
        if "postgresql+asyncpg://" in url:
            return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        return url

    @property
    def database_url_async(self) -> str:
        """Get asynchronous database URL."""
        return str(self.DATABASE_URL)


# Global settings instance
settings = Settings()

