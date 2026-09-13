"""Tenant-scoped reply execution and cancellation contracts."""

from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.schemas.reply_phase import ReplyPhaseIdentity

ReplyStatus = Literal[
    "active", "publishing", "cancel_requested", "cancelled", "completed", "failed"
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
