"""Redacted operator view of unresolved session command delivery."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SessionCommandAlertItem(BaseModel):
    operation_id: UUID
    project_id: UUID
    kind: str
    notification_status: str
    effects_status: str
    notification_attempts: int
    effects_attempts: int
    notification_error: str | None
    effects_error: str | None
    created_at: datetime
    notification_updated_at: datetime


class SessionCommandAlertReport(BaseModel):
    checked_at: datetime
    stale_after_seconds: int
    offset: int
    total: int
    items: list[SessionCommandAlertItem]


class ReconcileSessionNotice(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_status: Literal["sending", "unknown"]
    reason: str = Field(min_length=5, max_length=500)
