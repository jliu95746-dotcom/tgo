"""Visitor-facing fields; never inherit the administrator channel schema."""

from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationInfo, field_validator

from app.models.platform import PlatformType
from app.schemas.base import BaseSchema


class PublicWidgetConfig(BaseSchema):
    """Explicit allowlist of values rendered by the website widget."""

    position: Literal[
        "bottom-right", "bottom-left", "top-right", "top-left"
    ] | None = None
    theme_color: str | None = None
    widget_title: str | None = None
    welcome_message: str | None = None
    logo_url: str | None = None
    display_mode: Literal["small", "big"] | None = None

    @field_validator("*", mode="before")
    @classmethod
    def public_text_only(cls, value: object, info: ValidationInfo) -> object:
        # Historical config is untyped JSON. Do not serialize nested objects
        # or break widget initialization on malformed presentation values.
        if not isinstance(value, str):
            return None
        if info.field_name == "position" and value not in {
            "bottom-right",
            "bottom-left",
            "top-right",
            "top-left",
        }:
            return None
        if info.field_name == "display_mode" and value not in {"small", "big"}:
            return None
        return value


class PublicPlatformResponse(BaseSchema):
    """Public identity and presentation, excluding integration credentials."""

    id: UUID
    name: str
    display_name: str
    type: PlatformType
    is_active: bool
    service_available: bool = True
    logo_url: str | None = None
    config: PublicWidgetConfig = Field(default_factory=PublicWidgetConfig)
