"""Operator-only projection of the AI service's internal usage inventory."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class ModelUsageResponse(BaseModel):
    id: UUID
    project_id: UUID
    reservation_id: UUID | None
    model_name: str
    purpose: str
    status: Literal["running", "succeeded", "failed", "cancelled"]
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_fen: Decimal | None
    currency: Literal["CNY"] = "CNY"
    created_at: datetime
    completed_at: datetime | None
