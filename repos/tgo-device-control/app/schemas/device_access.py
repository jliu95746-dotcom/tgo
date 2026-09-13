"""Authenticated internal device access; distinct from staff login tokens."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class DeviceServicePrincipal(BaseModel):
    sub: Literal["tgo-api", "tgo-ai"]
    project_id: UUID
    device_id: UUID | None = None
    session_id: UUID | None = None
