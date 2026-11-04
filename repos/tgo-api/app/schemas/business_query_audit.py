"""Safe business query audit metadata shown in workflow history."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BusinessQueryAuditItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    query_id: UUID
    agent_id: UUID
    operation: Literal["order_query", "logistics_query", "customer_profile_query", "membership_query"]
    outcome: Literal["success", "denied", "timeout", "failed"]
    duration_ms: float = Field(ge=0)
    created_at: datetime


class BusinessQueryAuditList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[BusinessQueryAuditItem] = Field(max_length=20)
    total: int = Field(ge=0)
