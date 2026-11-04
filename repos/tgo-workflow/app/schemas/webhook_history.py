"""Bounded public source records without input, credential or replay proofs."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.webhook_delivery import DeliveryStatus


class WebhookDeliveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    binding_revision: int
    credential_revision: int
    release_id: str
    status: DeliveryStatus
    attempts: int
    error_code: str | None
    execution_id: str | None
    execution_status: str | None
    created_at: datetime
    expires_at: datetime


class WebhookDeliveryList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    workflow_id: str
    items: list[WebhookDeliveryRecord]
    total: int
    limit: int
    offset: int
    has_more: bool
