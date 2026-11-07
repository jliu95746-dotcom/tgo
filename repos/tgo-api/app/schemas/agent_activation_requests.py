"""Immutable activation intent and historical receipt; neither grants execution access."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.agent_activation import AgentActivationSelection


class ActivationPolicyExpectation(AgentActivationSelection):
    policy_revision: int = Field(ge=1, strict=True)
    requirements_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class ActivationExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checked_at: datetime
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_owner_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    business_evidence_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    channels: list[ActivationPolicyExpectation] = Field(
        min_length=1, max_length=5
    )

    @model_validator(mode="after")
    def explicit_current_scope(self) -> "ActivationExpectation":
        if self.checked_at.tzinfo is None or len(
            {row.channel for row in self.channels}
        ) != len(self.channels):
            raise ValueError(
                "Activation expectation requires an explicit time and unique channels"
            )
        return self


class AgentActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    expected: ActivationExpectation


ActivationFailure = Literal[
    "configuration_changed",
    "not_ready",
    "confirmation_expired",
    "permission_changed",
    "unavailable",
]


class AgentActivationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal[
        "employee_activation_request"
    ] = "employee_activation_request"
    can_enable: Literal[False] = False
    project_id: UUID
    requested_by: UUID
    agent_id: UUID
    request_key: UUID
    status: Literal["completed", "rejected", "not_found"]
    command: AgentActivationRequest | None = None
    finished_at: datetime | None = None
    failure_code: ActivationFailure | None = None

    @model_validator(mode="after")
    def coherent_outcome(self) -> "AgentActivationReceipt":
        if self.status == "not_found":
            if self.command or self.finished_at or self.failure_code:
                raise ValueError("Missing receipt cannot claim an outcome")
        elif (
            not self.command
            or self.command.request_key != self.request_key
            or not self.finished_at
            or self.finished_at.tzinfo is None
        ):
            raise ValueError(
                "Terminal receipt requires immutable command and time"
            )
        if (self.status == "rejected") != (self.failure_code is not None):
            raise ValueError("Receipt failure status mismatch")
        return self
