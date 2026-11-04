"""Short-lived local capability observation; never an enablement grant."""

from datetime import timedelta
from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.schemas.agent_device_script_selection import ScriptSelectionRecord
from app.schemas.device_script_policy import ScriptDescriptor


class ScriptRuntimeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal[
        "employee_script_runtime_definition"
    ] = "employee_script_runtime_definition"
    can_enable: Literal[False] = False
    selection: ScriptSelectionRecord
    descriptor: ScriptDescriptor
    remaining_runs: int = Field(strict=True, ge=0, le=100)
    checked_at: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def observed_readonly_definition(self) -> Self:
        if (
            not self.selection.enabled
            or self.descriptor.effect != "read_only"
            or self.valid_until <= self.checked_at
            or self.valid_until > self.checked_at + timedelta(seconds=30)
            or self.valid_until > self.selection.expires_at
        ):
            raise ValueError("Invalid current script observation")
        return self
