"""Explicit case versions and controlled readable evidence, never approval."""

import re
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
    ScriptTrialStart,
    ScriptTrialReport,
    BusinessValue,
    Digest,
    _wire_size,
)


class ScriptCaseValues(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    parameters: dict[str, BusinessValue] = Field(max_length=16, repr=False)
    expected_fields: dict[str, BusinessValue] = Field(
        min_length=1, max_length=32, repr=False
    )

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", name)
            for name in self.parameters
        ):
            raise ValueError("Invalid business field name")
        if (
            max(_wire_size(self.parameters), _wire_size(self.expected_fields))
            > 65536
        ):
            raise ValueError("Business values exceed the wire budget")
        return self


class ScriptCaseWrite(ScriptTrialStart):
    case_id: UUID | None = None
    expected_revision: int = Field(ge=0, le=2147483646, strict=True)
    title: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def versioned(self) -> Self:
        if (self.case_id is None) != (self.expected_revision == 0):
            raise ValueError("New cases need revision zero")
        if not self.title.strip() or any(
            ord(character) < 32 for character in self.title
        ):
            raise ValueError("Case title is required and must be one line")
        self.title.encode("utf-8")
        return self


class ScriptCaseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal[
        "employee_device_script_case"
    ] = "employee_device_script_case"
    can_enable: Literal[False] = False
    id: UUID
    case_id: UUID
    project_id: UUID
    agent_id: UUID
    created_by: UUID
    request_id: UUID
    revision: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1, max_length=80)
    binding_id: UUID
    binding_revision: int = Field(ge=1, strict=True)
    device_id: UUID
    policy_digest: Digest
    grant_digest: Digest
    case_digest: Digest
    status: Literal["current", "changed", "superseded", "expired"]
    created_at: AwareDatetime
    expires_at: AwareDatetime


class ScriptCaseRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    case: ScriptCaseRecord
    values: ScriptCaseValues = Field(repr=False)


class ScriptCasePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: UUID
    agent_id: UUID
    items: list[ScriptCaseRecord]
    total: int = Field(ge=0)
    skip: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)


class ScriptResultValues(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    report_id: UUID
    reference_digest: Digest
    case_digest: Digest
    fields: dict[str, BusinessValue] = Field(max_length=32, repr=False)


class ScriptTrialEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal[
        "employee_device_script_evidence"
    ] = "employee_device_script_evidence"
    can_enable: Literal[False] = False
    project_id: UUID
    actor_id: UUID
    agent_id: UUID
    report_id: UUID
    status: Literal[
        "available", "pending", "unavailable", "expired", "not_retained"
    ]
    case: ScriptCaseRecord | None
    values: ScriptCaseValues | None = Field(repr=False)
    result_fields: dict[str, BusinessValue] | None = Field(repr=False)
    captured_at: AwareDatetime | None
    expires_at: AwareDatetime | None

    @model_validator(mode="after")
    def readable(self) -> Self:
        if self.case is not None and (
            self.case.project_id != self.project_id
            or self.case.agent_id != self.agent_id
        ):
            raise ValueError("Case and evidence ownership disagree")
        if self.status == "available" and (
            self.case is None
            or self.values is None
            or self.result_fields is None
            or self.captured_at is None
            or self.expires_at is None
        ):
            raise ValueError("Readable evidence needs exact case and result")
        if self.status != "available" and (
            self.values is not None or self.result_fields is not None
        ):
            raise ValueError("Unavailable evidence cannot expose bodies")
        return self


class ScriptCaseTrialResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal[
        "employee_device_script_case_trial"
    ] = "employee_device_script_case_trial"
    can_enable: Literal[False] = False
    case_version_id: UUID
    report: ScriptTrialReport


class ScriptCaseSaveControl(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    binding_id: UUID
    binding_revision: int = Field(ge=1, strict=True)
    case_id: UUID | None
    expected_revision: int = Field(ge=0, le=2147483646, strict=True)

    @model_validator(mode="after")
    def versioned_control(self) -> Self:
        if (self.case_id is None) != (self.expected_revision == 0):
            raise ValueError("New cases need revision zero")
        return self


class ScriptCaseSaveState(ScriptCaseSaveControl):
    scope: Literal[
        "employee_device_script_case_save"
    ] = "employee_device_script_case_save"
    can_enable: Literal[False] = False
    project_id: UUID
    actor_id: UUID
    agent_id: UUID
    status: Literal["preparing", "saved", "failed", "cancelled"]
    case: ScriptCaseRecord | None
    created_at: AwareDatetime
    deadline_at: AwareDatetime
    completed_at: AwareDatetime | None
    error_code: Literal[
        "preparation_failed", "preparation_expired", "cancelled"
    ] | None

    @model_validator(mode="after")
    def exact_save_outcome(self) -> Self:
        if self.status == "preparing":
            if (
                self.case is not None
                or self.completed_at is not None
                or self.error_code is not None
            ):
                raise ValueError("Preparing save has no terminal outcome")
        elif self.completed_at is None:
            raise ValueError("Terminal save needs a completion time")
        if self.status == "saved":
            row = self.case
            if (
                row is None
                or self.error_code is not None
                or row.project_id != self.project_id
                or row.agent_id != self.agent_id
                or row.created_by != self.actor_id
                or row.request_id != self.request_id
                or row.binding_id != self.binding_id
                or row.binding_revision != self.binding_revision
                or row.revision != self.expected_revision + 1
                or self.case_id is not None
                and row.case_id != self.case_id
            ):
                raise ValueError(
                    "Saved outcome needs the exact version receipt"
                )
        elif self.case is not None:
            raise ValueError("Uncommitted save cannot expose a version")
        if self.status == "failed" and self.error_code not in {
            "preparation_failed",
            "preparation_expired",
        }:
            raise ValueError("Failed save needs a bounded error code")
        if self.status == "cancelled" and self.error_code != "cancelled":
            raise ValueError("Cancelled save needs cancellation evidence")
        return self
