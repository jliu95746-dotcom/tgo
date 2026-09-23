"""Separate operator-facing model metadata from private execution credentials."""

from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
)


class PlatformModelDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=150)
    provider_kind: Literal["openai", "openai_compatible", "anthropic", "google"]
    api_base_url: str | None = Field(default=None, max_length=255)
    vendor: str | None = Field(default=None, max_length=40)
    active: bool = True
    input_fen_per_million: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=6
    )
    output_fen_per_million: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=6
    )

    @field_validator("api_base_url")
    @classmethod
    def valid_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        url = urlsplit(value)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("请填写不含凭据或查询参数的模型服务地址")
        if url.scheme == "http" and url.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("远程模型服务必须使用 HTTPS")
        return value.rstrip("/")

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        value = value.strip()
        if not value or value == "__system_default__":
            raise ValueError("请填写实际模型名称")
        return value


class PlatformModelChange(PlatformModelDefinition):
    expected_version: int = Field(ge=0)
    api_key: SecretStr | None = Field(default=None)
    reason: str = Field(min_length=5, max_length=500)


class PlatformModelPolicy(BaseModel):
    version: int
    definition: PlatformModelDefinition | None
    has_api_key: bool


class StoredPlatformModel(BaseModel):
    version: int
    definition: PlatformModelDefinition
    encrypted_api_key: str


class PlatformModelRuntime(PlatformModelDefinition):
    """Only returned through authenticated private service APIs."""

    api_key: SecretStr

    @field_serializer("api_key")
    def internal_key(self, value: SecretStr) -> str:
        return value.get_secret_value()
