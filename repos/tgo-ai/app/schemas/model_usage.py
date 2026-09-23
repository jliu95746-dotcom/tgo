"""Platform cost estimates; these units never replace customer reply credits."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ModelCostRate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_fen_per_million: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    output_fen_per_million: Decimal = Field(ge=0, max_digits=18, decimal_places=6)


class ModelUsageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
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
