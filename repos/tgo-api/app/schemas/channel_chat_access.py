"In-process channel authority; private credentials never enter replies \
or jobs."

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ChannelChatAccess(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: UUID
    platform_id: UUID
    visitor_id: UUID | None = None
    platform_api_key: SecretStr = Field(repr=False, exclude=True)
    service_token: SecretStr = Field(repr=False, exclude=True)
