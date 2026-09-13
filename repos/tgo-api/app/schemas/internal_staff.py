"""Minimal staff context exposed to internal AI tools, never login credentials."""

from typing import Literal
from uuid import UUID

from app.models.staff import StaffRole, StaffStatus
from app.schemas.base import BaseSchema


class InternalStaffResponse(BaseSchema):
    """Allowlisted display and availability fields for an existing staff member."""

    id: UUID
    type: Literal["staff"] = "staff"
    name: str | None = None
    nickname: str | None = None
    avatar_url: str | None = None
    description: str | None = None
    role: StaffRole
    status: StaffStatus
    is_active: bool
    service_paused: bool
