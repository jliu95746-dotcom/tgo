"Visitor credentials are not platform identifiers or business execution \
grants."

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.schemas.business_execution_context import BusinessCustomerSessionInput


class VisitorAccessInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visitor_access_token: SecretStr | None = Field(
        default=None, max_length=256, repr=False, exclude=True
    )
    im_token: SecretStr | None = Field(
        default=None, max_length=8192, repr=False, exclude=True
    )
    customer_sessions: list[BusinessCustomerSessionInput] = Field(
        default_factory=list, max_length=20, repr=False, exclude=True
    )


class VisitorIMBusinessReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope_id: UUID
    scope_revision: int = Field(ge=1, strict=True)
    connection_id: UUID
    binding_id: UUID
    binding_revision: int = Field(ge=1, strict=True)


class VisitorBusinessAccess(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    references: list[VisitorIMBusinessReference]
    expires_at: datetime | None


class VisitorIMAccessClaims(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    sub: UUID
    project_id: UUID
    platform_id: UUID
    type: Literal["visitor_im"]
    aud: Literal["yujian-visitor-im"]
    exp: int = Field(strict=True)
    access_revision: int | None = Field(default=None, ge=1, strict=True)
    business_references: list[VisitorIMBusinessReference] = Field(
        default_factory=list, max_length=20
    )


class VisitorAccessEndRequest(VisitorAccessInput):
    platform_api_key: str = Field(
        min_length=1, max_length=255, repr=False, exclude=True
    )
    visitor_id: UUID
    visitor_end_token: SecretStr | None = Field(
        default=None, max_length=8192, repr=False, exclude=True
    )


class VisitorEndGrantClaims(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    sub: UUID
    project_id: UUID
    platform_id: UUID
    type: Literal["visitor_end"]
    aud: Literal["yujian-visitor-end"]
    exp: int = Field(strict=True)
    access_revision: int = Field(ge=1, strict=True)
    holder_digest: str = Field(
        pattern=r"^[0-9a-f]{64}$", repr=False, exclude=True
    )


class VisitorAccessEndResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["ended"] = "ended"
    upstream_stop_status: Literal["confirmed", "pending"]
    im_disconnect_status: Literal["requested", "pending"]
