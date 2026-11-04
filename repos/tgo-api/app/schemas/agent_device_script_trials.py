"""Typed private trial inputs and metadata-only, non-approving reports."""

import json
import re
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field,
    StrictBool, StrictInt, StrictStr, model_validator,
)

BusinessValue = StrictBool | StrictInt | StrictStr
Digest = Annotated[
    str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
]


def _wire_size(values: dict[str, BusinessValue]) -> int:
    encoded = json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    for char, escaped in (
        ("&", r"\u0026"), ("<", r"\u003c"), (">", r"\u003e"),
        ("\u2028", r"\u2028"), ("\u2029", r"\u2029"),
    ):
        encoded = encoded.replace(char, escaped)
    return len(encoded.encode("utf-8"))


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
        if any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", name)
            for name in self.parameters
        ):
            raise ValueError("Invalid business field name")
        largest = max(
            _wire_size(self.parameters), _wire_size(self.expected_fields)
        )
        if largest > 65536:
            raise ValueError("Business values exceed the wire budget")
        return self


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
    status: Literal["unknown", "matched", "mismatched", "failed"]
    error_code: Literal[
        "result_unknown", "configuration_changed", "connection_changed",
        "invalid_receipt", "result_unavailable", "execution_failed",
        "interrupted",
    ] | None
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
                raise ValueError("Comparison disagrees with fixed expectation")
        if self.status == "failed" and self.error_code is None:
            raise ValueError("Failure needs an explicit category")
        return self
