"""Business execution receipts use their own durable request identity."""

from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.schemas.agent_device_script_trials import (
    BusinessValue,
    Digest,
    _wire_size,
)
import re

RunStatus = Literal[
    "preparing", "unknown", "succeeded", "failed", "cancelled"
]
RunError = Literal[
    "preparation_expired",
    "not_dispatched",
    "conditions_changed",
    "result_unknown",
    "invalid_receipt",
    "execution_failed",
    "result_unavailable",
    "cancelled",
]


class ScriptRunControl(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    report_id: UUID
    review_id: UUID


class ScriptRunStart(ScriptRunControl):
    parameters: dict[str, BusinessValue] = Field(
        max_length=16, repr=False
    )

    @model_validator(mode="after")
    def bounded_input(self) -> Self:
        if any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", name)
            for name in self.parameters
        ):
            raise ValueError("Invalid business field name")
        if _wire_size(self.parameters) > 65536:
            raise ValueError("Business values exceed the wire budget")
        return self


class ScriptRunView(ScriptRunControl):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal[
        "employee_device_script_run"
    ] = "employee_device_script_run"
    can_enable: Literal[False] = False
    id: UUID
    project_id: UUID
    actor_id: UUID
    agent_id: UUID
    execution_id: UUID | None
    status: RunStatus
    error_code: RunError | None
    created_at: AwareDatetime
    deadline_at: AwareDatetime
    dispatch_started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    stop_requested_at: AwareDatetime | None
    result_digest: Digest | None
    result_fields: dict[str, BusinessValue] | None = Field(
        default=None,
        max_length=32,
        repr=False,
    )

    @model_validator(mode="after")
    def consistent_state(self) -> Self:
        if self.deadline_at <= self.created_at:
            raise ValueError("Invalid preparation deadline")
        if self.status in {"unknown", "succeeded"} and (
            self.execution_id is None
            or self.dispatch_started_at is None
        ):
            raise ValueError("Missing dispatch identity")
        if self.status in {"succeeded", "failed", "cancelled"}:
            if self.completed_at is None:
                raise ValueError("Missing terminal timestamp")
        elif self.completed_at is not None:
            raise ValueError("Unconfirmed terminal timestamp")
        if self.result_fields is not None and (
            self.status != "succeeded" or not self.result_digest
        ):
            raise ValueError("Unconfirmed result body")
        if self.result_fields is not None and (
            _wire_size(self.result_fields) > 65536
        ):
            raise ValueError("Business result exceeds the wire budget")
        return self
