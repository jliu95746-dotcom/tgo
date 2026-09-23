"""Explicit company member mutations; no client tenant identifiers."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    role: Literal["admin", "user"] = "user"


class InvitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    role: str
    status: str
    expires_at: datetime
    created_at: datetime


class MemberChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["admin", "user"] | None = None
    account_enabled: bool | None = None
    transfer_to: UUID | None = None
    return_to_queue: bool = False


class SeatResponse(BaseModel):
    used: int
    reserved: int
    limit: int | None


class CompanyFeatureStatus(BaseModel):
    enabled: bool
