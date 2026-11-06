"""Staff review contracts exclude executable checkpoint data."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkflowReconciliationNode(BaseModel):
    id: UUID
    node_id: str
    node_type: str
    label: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None


class WorkflowReconciliationSummary(BaseModel):
    id: UUID
    execution_id: UUID
    workflow_id: UUID
    summary: str
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    requested_at: datetime
    can_continue: bool
    blocked_reason: Literal[
        "unknown_result",
        "progress_missing",
        "record_changed",
        "key_unavailable",
    ] | None
    nodes: list[WorkflowReconciliationNode]


class WorkflowReconciliationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["continue", "stop"]
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkflowReconciliationDecisionResult(BaseModel):
    id: UUID
    execution_id: UUID
    decision: Literal["continue", "stop"]
    status: Literal["pending_resume", "cancelled"]
    decided_at: datetime
    dispatch_status: Literal["queued", "unknown", "not_applicable"]
