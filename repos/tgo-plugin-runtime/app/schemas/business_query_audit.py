"""PII-free query audit metadata for internal monitoring."""

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
    operation: Literal[
        "order_query",
        "logistics_query",
        "customer_profile_query",
        "membership_query",
    ]
    outcome: Literal["success", "denied", "timeout", "failed"]
    duration_ms: float = Field(ge=0)
    created_at: datetime


class BusinessQueryAuditList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[BusinessQueryAuditItem] = Field(max_length=20)
    total: int = Field(ge=0)


class BusinessMessageAuditQuery(BaseModel):
    """Private lookup scope copied from a trusted API interaction record."""

    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    source_message_id: str = Field(min_length=1, max_length=255)
    response_client_msg_no: str = Field(min_length=1, max_length=100)
    limit: int = Field(default=20, ge=1, le=20)
    skip: int = Field(default=0, ge=0)
