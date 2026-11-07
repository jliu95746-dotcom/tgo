"""Tenant-scoped reply execution and cancellation contracts."""

from typing import Annotated, Literal
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
)

from app.schemas.reply_phase import ReplyPhaseIdentity

ReplyStatus = Literal[
    "active",
    "publishing",
    "cancel_requested",
    "cancelled",
    "completed",
    "failed",
]
ReplyFailure = Literal[
    "generation_failed", "control_unavailable", "upstream_stop_unconfirmed"
]


class ReplyRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str = Field(min_length=1, max_length=255)
    client_msg_no: str = Field(min_length=1, max_length=255)
    channel_id: str = Field(min_length=1, max_length=255)
    channel_type: int
    generation: UUID = Field(default_factory=uuid4)
    status: ReplyStatus = "active"
    failure_reason: ReplyFailure | None = None
    phase: ReplyPhaseIdentity | None = Field(default=None, repr=False)
    phase_ended: bool = False


class StaffCancelRequest(BaseModel):
    client_msg_no: str = Field(min_length=1, max_length=255)
    reason: str | None = Field(default=None, max_length=500)


class CancelByClientNoRequest(StaffCancelRequest):
    platform_api_key: str = Field(min_length=1, max_length=255)


class ReplyCancelResponse(BaseModel):
    accepted: Literal[True] = True
    status: Literal["cancelled"] = "cancelled"
    client_msg_no: str


class SupervisorCancelResponse(BaseModel):
    run_id: str
    cancelled: StrictBool


class AIExecutionAttemptLookup(BaseModel):
    """Locate a saved customer message without accepting a tenant ID."""

    model_config = ConfigDict(extra="forbid")
    channel_id: str = Field(min_length=1, max_length=255)
    source_message_id: str = Field(min_length=1, max_length=255)


class AIExecutionAttemptSummary(BaseModel):
    """Private run metadata; no prompt, tool argument or customer text."""

    model_config = ConfigDict(from_attributes=True)
    execution_id: UUID
    status: Literal["running", "completed", "failed"]
    started_at: datetime
    completed_at: datetime | None


class AIExecutionToolEventSummary(BaseModel):
    """Private event order and outcome without tool arguments or results."""

    model_config = ConfigDict(from_attributes=True)
    sequence_no: int
    tool_name: str
    tool_call_id: str | None
    status: Literal["started", "completed", "failed"]
    occurred_at: datetime


class AIExecutionBusinessQuerySummary(BaseModel):
    """Private link to the redacted plugin audit, without customer payloads."""

    model_config = ConfigDict(from_attributes=True)
    query_id: UUID
    operation: Literal[
        "order_query",
        "logistics_query",
        "customer_profile_query",
        "membership_query",
    ]
    outcome: Literal["passed", "failed"]
    created_at: datetime


class AIExecutionBusinessQuerySignal(BaseModel):
    """Private AI tool completion metadata; never a customer reply payload."""

    model_config = ConfigDict(extra="forbid")
    tool_name: str = Field(min_length=1, max_length=200)
    query_id: UUID
    operation: Literal[
        "order_query",
        "logistics_query",
        "customer_profile_query",
        "membership_query",
    ]
    outcome: Literal["passed", "failed"]


class AIReplyOriginRequest(BaseModel):
    """Bounded channel lookup; tenant identity comes from the staff token."""

    model_config = ConfigDict(extra="forbid")
    channel_id: str = Field(min_length=1, max_length=255)
    client_msg_nos: list[
        Annotated[str, StringConstraints(min_length=1, max_length=100)]
    ] = Field(
        min_length=1,
        max_length=50,
    )


class VisitorAIReplyOriginRequest(AIReplyOriginRequest):
    (
        'The public platform key locates a site; the IM grant authorizes its '
        'visitor.'
    )

    platform_api_key: str = Field(min_length=1, max_length=255)


AIReplyOriginStatus = Literal[
    "verified", "unrecorded", "missing", "mismatch", "unavailable"
]


class AIReplyOriginEvidence(BaseModel):
    """A label is safe to show only when status is verified."""

    client_msg_no: str
    status: AIReplyOriginStatus
