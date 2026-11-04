"""A fixed approval selection is configuration, not execution authority."""

from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class ScriptSelectionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    expected_revision: int = Field(strict=True, ge=0, lt=2147483647)
    enabled: bool = Field(default=True, strict=True)
    report_id: UUID | None = None
    review_id: UUID | None = None

    @model_validator(mode="after")
    def exact_operation(self) -> Self:
        if self.enabled:
            if self.report_id is None or self.review_id is None:
                raise ValueError("A fixed report and review are required")
        elif self.report_id is not None or self.review_id is not None:
            raise ValueError("Omit approval pointers when clearing selection")
        return self


class ScriptSelectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal[
        "employee_device_script_selection"
    ] = "employee_device_script_selection"
    can_enable: Literal[False] = False
    id: UUID
    project_id: UUID
    agent_id: UUID
    request_id: UUID
    configured_by: UUID
    revision: int = Field(strict=True, ge=1, le=2147483647)
    enabled: bool = Field(strict=True)
    binding_id: UUID
    binding_revision: int = Field(strict=True, ge=1, le=2147483647)
    report_id: UUID
    review_id: UUID
    saved_at: AwareDatetime
    expires_at: AwareDatetime
    selection_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_time(self) -> Self:
        if self.enabled and self.expires_at <= self.saved_at:
            raise ValueError(
                "Selected approval must outlive its saved version"
            )
        return self


class ScriptSelectionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal[
        "employee_device_script_selection_state"
    ] = "employee_device_script_selection_state"
    can_enable: Literal[False] = False
    project_id: UUID
    actor_id: UUID
    agent_id: UUID
    selection: ScriptSelectionRecord | None
    status: Literal["unselected", "selected", "changed", "expired", "revoked"]

    @model_validator(mode="after")
    def consistent_scope(self) -> Self:
        row = self.selection
        if row is not None and (
            row.project_id != self.project_id or row.agent_id != self.agent_id
        ):
            raise ValueError("Selection belongs to a different scope")
        if (row is None or not row.enabled) != (self.status == "unselected"):
            raise ValueError("Inconsistent selection status")
        if self.status == "selected" and (
            row is None or row.configured_by != self.actor_id
        ):
            raise ValueError("Selected approval has a different owner")
        return self
