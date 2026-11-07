"An in-request proof for website data, never a response or a persisted \
grant."

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class VisitorDataAccess(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    visitor_id: UUID
    project_id: UUID
    platform_id: UUID
    platform_api_key: SecretStr = Field(repr=False, exclude=True)
    im_token: SecretStr = Field(repr=False, exclude=True)
