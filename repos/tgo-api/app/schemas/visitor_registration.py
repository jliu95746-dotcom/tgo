"""The website registration response never inherits internal staff fields."""

from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
)

from app.core.config import settings


class VisitorRegisterResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True, validate_assignment=True, extra="forbid"
    )

    id: UUID
    project_id: UUID
    platform_id: UUID
    platform_open_id: str
    name: str | None = None
    nickname: str | None = None
    nickname_zh: str | None = None
    display_nickname: str | None = None
    avatar_url: str | None = None
    is_online: bool
    first_visit_time: datetime
    last_visit_time: datetime
    last_offline_time: datetime | None = None
    created_at: datetime
    updated_at: datetime
    channel_id: str
    channel_type: int = 251
    im_token: SecretStr = Field(repr=False)
    visitor_access_token: SecretStr = Field(repr=False)
    visitor_end_token: SecretStr = Field(repr=False)
    business_protected: bool = False
    business_access_expires_at: datetime | None = None

    @field_validator("avatar_url", mode="after")
    @classmethod
    def resolve_registration_avatar_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        if value.startswith(("http://", "https://")):
            return value
        return f"{settings.API_BASE_URL.rstrip('/')}{value}"

    @field_serializer("im_token", "visitor_access_token", "visitor_end_token")
    def serialize_registration_secret(self, value: SecretStr) -> str:
        """Issued credentials are sent only in the authorized registration reply."""
        return value.get_secret_value()
