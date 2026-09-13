"""Minimum device identity needed to validate an AI agent binding."""

from uuid import UUID

from pydantic import BaseModel


class BoundDeviceIdentity(BaseModel):
    id: UUID
    project_id: UUID
