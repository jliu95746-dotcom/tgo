"""Immutable original actors for file reads and upload continuations."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.visitor_access import VisitorIMAccessClaims


class StaffFileActor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    project_id: UUID
    token_version: int = Field(ge=1, strict=True)
    username: str
    expires_at: int | None = Field(default=None, ge=1, strict=True)


class VisitorFileActor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    project_id: UUID
    platform_id: UUID
    expires_at: int | None = Field(default=None, ge=1, strict=True)
    im_claims: VisitorIMAccessClaims = Field(repr=False, exclude=True)
    platform_key_digest: str = Field(
        pattern=r"^[0-9a-f]{64}$", repr=False, exclude=True
    )
