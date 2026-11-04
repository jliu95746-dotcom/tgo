"""Original request lifecycle and physical-stop proof without private input."""

from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, model_validator,
)

from app.schemas.agent_device_script_trials import ScriptTrialReport

PreparationError = Literal[
    "preparation_timeout", "preparation_failed", "interrupted", "cancelled"
]


class ScriptTrialControl(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    binding_id: UUID
    binding_revision: int = Field(ge=1, le=2147483647, strict=True)


class ScriptTrialRequestState(ScriptTrialControl):
    scope: Literal[
        "employee_device_script_trial_request"
    ] = "employee_device_script_trial_request"
    can_enable: Literal[False] = False
    project_id: UUID
    actor_id: UUID
    agent_id: UUID
    status: Literal["preparing", "ready", "failed", "cancelled"]
    stop_status: Literal["none", "requested", "confirmed", "finished"]
    created_at: AwareDatetime
    deadline_at: AwareDatetime
    completed_at: AwareDatetime | None
    error_code: PreparationError | None
    report: ScriptTrialReport | None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.status == "ready":
            if (
                self.report is None or self.completed_at is None
                or self.error_code is not None
            ):
                raise ValueError("Prepared request needs its original report")
            if any(
                getattr(self.report, key) != getattr(self, key)
                for key in (
                    "project_id", "actor_id", "agent_id", "request_id",
                    "binding_id", "binding_revision",
                )
            ):
                raise ValueError("Request and report scopes disagree")
        elif self.status == "preparing":
            if (
                self.report is not None or self.completed_at is not None
                or self.error_code is not None or self.stop_status != "none"
            ):
                raise ValueError("Preparation cannot contain a final result")
        elif (
            self.report is not None or self.completed_at is None
            or self.error_code is None
        ):
            raise ValueError("Request termination needs an explicit outcome")
        if (self.status == "cancelled") != (self.error_code == "cancelled"):
            raise ValueError("Cancellation category disagrees with status")
        if self.status == "cancelled" and self.stop_status != "confirmed":
            raise ValueError("Undispatched cancellation must be confirmed")
        if self.status == "failed" and self.stop_status != "none":
            raise ValueError("Preparation failure is not a device stop")
        return self
