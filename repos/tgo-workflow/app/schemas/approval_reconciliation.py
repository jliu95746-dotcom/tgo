"""Only scoped summaries and staff decisions cross the reconciliation API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReconciliationDecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation: int = Field(ge=0)
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["continue", "stop"]
    decided_by: str
    decided_at: datetime
    status: Literal["pending_resume", "cancelled"]
    checkpoint_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class ReconciliationLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    generation: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    decisions: list[ReconciliationDecisionRecord] = Field(
        default_factory=list, max_length=256
    )


class ReconciliationNodeSummary(BaseModel):
    id: str
    node_id: str
    node_type: str
    label: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None


class ReconciliationSummary(BaseModel):
    id: str
    execution_id: str
    workflow_id: str
    summary: str
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    requested_at: datetime
    can_continue: bool
    blocked_reason: (
        Literal[
            "unknown_result",
            "progress_missing",
            "record_changed",
            "key_unavailable",
        ]
        | None
    )
    nodes: list[ReconciliationNodeSummary]


class ReconciliationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["continue", "stop"]
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReconciliationDecisionResult(BaseModel):
    id: str
    execution_id: str
    decision: Literal["continue", "stop"]
    status: Literal["pending_resume", "cancelled"]
    decided_at: datetime
    dispatch_status: Literal[
        "queued", "unknown", "not_applicable"
    ] = "not_applicable"
