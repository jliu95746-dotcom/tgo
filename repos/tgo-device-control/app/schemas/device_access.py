"""Authenticated internal device access; distinct from staff login tokens."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class DeviceServicePrincipal(BaseModel):
    sub: Literal["tgo-api", "tgo-ai"]
    project_id: UUID
    device_id: UUID | None = None
    session_id: UUID | None = None
    read_policy_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    script_policy_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
