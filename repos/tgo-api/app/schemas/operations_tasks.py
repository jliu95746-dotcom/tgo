"""Operator task diagnostics without payment credentials or customer payloads."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID | None
    order_id: UUID | None
    kind: str
    status: str
    attempts: int
    available_at: datetime
    locked_until: datetime | None
    last_error: str | None


class RetryTask(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=5, max_length=500)


class AuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID | None
    operator_id: UUID
    action: str
    reason: str
    created_at: datetime
