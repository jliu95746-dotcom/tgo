"""Private per-phase capability shared by the gateway and AI service."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReplyPhaseIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str = Field(min_length=1, max_length=255)
    client_msg_no: str = Field(min_length=1, max_length=255)
    generation: UUID
    phase_id: UUID
    proof: UUID = Field(repr=False)


class ReplyPhaseReceipt(ReplyPhaseIdentity):
    status: Literal["ended"]


class ReplyPhaseAcknowledgment(BaseModel):
    accepted: Literal[True] = True
    phase_id: UUID
