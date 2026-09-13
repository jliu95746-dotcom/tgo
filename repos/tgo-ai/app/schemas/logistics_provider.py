"""Configuration for user-owned HTTP logistics providers (no store dependency)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LogisticsProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_name: str = Field(min_length=1, max_length=100)
    provider_kind: Literal["custom", "kuaidi100", "kdniao"] = "custom"
    account_id: str = Field(default="", max_length=128)
    method: Literal["GET", "POST"] = "GET"
    body_format: Literal["json", "form"] = "json"
    auth_type: Literal["none", "appcode", "bearer", "header"] = "appcode"
    auth_header: str = "Authorization"
    credential: str = Field(default="", repr=False, max_length=4096)
    credential_encrypted: str = Field(default="", repr=False)
    credential_configured: bool = False
    credential_endpoint: str = ""
    tracking_param: str = Field(default="tracking_no", min_length=1, max_length=100)
    fixed_params: dict[str, str] = Field(default_factory=dict)
    success_path: str = Field(default="success", min_length=1, max_length=200)
    success_value: str = Field(default="true", min_length=1, max_length=100)
    events_path: str = Field(default="events", min_length=1, max_length=200)
    time_field: str = Field(default="time", min_length=1, max_length=100)
    description_field: str = Field(default="description", min_length=1, max_length=100)
    carrier_path: str = Field(default="carrier_name", max_length=200)
    tracking_path: str = Field(default="", max_length=200)

    @field_validator("auth_header", "tracking_param")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not value or not all(
            c.isascii() and (c.isalnum() or c in "_-") for c in value
        ):
            raise ValueError("参数名称仅允许英文字母、数字、下划线和横线")
        return value

    @field_validator("fixed_params")
    @classmethod
    def bounded_params(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 30 or any(
            len(k) > 100 or len(v) > 1000 for k, v in value.items()
        ):
            raise ValueError("固定参数过多或过长")
        return value
