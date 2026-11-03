"""Private test inputs and metadata-only reports without approval."""

from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.schemas.device_script_budget import ScriptBudgetRead, Digest
from app.schemas.device_script_execution import (
    BusinessValue,
    ScriptExecuteBody,
    ScriptReceiptReference,
    canonical_script_values,
)
from app.schemas.device_script_policy import ScriptDescriptor
from app.schemas.device_session import DeviceExecutionIdentity

TrialStatus = Literal["unknown", "matched", "mismatched", "failed"]
TrialError = Literal[
    "result_unknown",
    "configuration_changed",
    "connection_changed",
    "invalid_receipt",
    "result_unavailable",
    "execution_failed",
    "interrupted",
]


class ScriptTrialStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    binding_id: UUID
    binding_revision: int = Field(ge=1, le=2147483647, strict=True)
    parameters: dict[str, BusinessValue] = Field(max_length=16, repr=False)
    expected_fields: dict[str, BusinessValue] = Field(
        min_length=1, max_length=32, repr=False
    )

    @model_validator(mode="after")
    def bounded_values(self) -> Self:
        ScriptExecuteBody(parameters=self.parameters)
        if len(canonical_script_values(self.expected_fields)) > 65536:
            raise ValueError("Expected result exceeds the wire budget")
        return self


class ScriptTrialReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    identity: DeviceExecutionIdentity
    execution_id: UUID
    policy_digest: Digest
    parameters_digest: Digest
    context_digest: Digest
    timeout_seconds: int = Field(ge=1, le=120, strict=True)
    max_output_bytes: int = Field(ge=256, le=65536, strict=True)
    descriptor: ScriptDescriptor
    before_budget: ScriptBudgetRead

    def query(self) -> ScriptReceiptReference:
        return ScriptReceiptReference(
            identity=self.identity,
            execution_id=self.execution_id,
            policy_digest=self.policy_digest,
            parameters_digest=self.parameters_digest,
            context_digest=self.context_digest,
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=self.max_output_bytes,
            descriptor_json=self.descriptor.model_dump_json(),
        )


class ScriptTrialReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal[
        "employee_device_script_trial"
    ] = "employee_device_script_trial"
    can_enable: Literal[False] = False
    assessment: Literal["not_reviewed"] = "not_reviewed"
    id: UUID
    project_id: UUID
    actor_id: UUID
    agent_id: UUID
    request_id: UUID
    device_id: UUID
    execution_id: UUID
    binding_id: UUID
    binding_revision: int = Field(ge=1, strict=True)
    policy_digest: Digest
    grant_digest: Digest
    expected_digest: Digest
    result_digest: Digest | None
    status: TrialStatus
    error_code: TrialError | None
    remaining_runs: int | None = Field(ge=0, le=100, strict=True)
    applicability: Literal["current", "changed", "expired", "unknown"]
    created_at: AwareDatetime
    completed_at: AwareDatetime | None
    expires_at: AwareDatetime
    dispatch_claimed_at: AwareDatetime

    @model_validator(mode="after")
    def consistent_result(self) -> Self:
        if self.status in {"matched", "mismatched"}:
            if (
                self.result_digest is None
                or self.completed_at is None
                or self.error_code is not None
            ):
                raise ValueError("Compared results require confirmed evidence")
            if (self.status == "matched") != (
                self.result_digest == self.expected_digest
            ):
                raise ValueError(
                    "Comparison does not match the fixed expectation"
                )
        if self.status == "failed" and self.error_code is None:
            raise ValueError("Failure needs an explicit category")
        return self
