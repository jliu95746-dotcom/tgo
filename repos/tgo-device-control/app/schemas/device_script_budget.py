"""Typed observations of reserved runs; no execution or approval authority."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[
    str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
]
ConnectionID = Annotated[
    str, Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
]
Count = Annotated[int, Field(strict=True, ge=0, le=100)]
Limit = Annotated[int, Field(strict=True, ge=1, le=100)]


class ScriptBudgetWire(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    session_id: ConnectionID
    policy_digest: Digest
    grant_digest: Digest
    policy_max_runs: Limit
    policy_used: Count
    grant_max_runs: Limit
    grant_used: Count

    @model_validator(mode="after")
    def consistent_counts(self) -> Self:
        if (
            self.policy_used > self.policy_max_runs
            or self.grant_used > self.grant_max_runs
            or self.policy_max_runs > self.grant_max_runs
            or self.policy_used > self.grant_used
        ):
            raise ValueError("Invalid script budget observation")
        return self


class ScriptBudgetRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal[
        "script_execution_budget_observation"
    ] = "script_execution_budget_observation"
    can_enable: Literal[False] = False
    project_id: UUID
    device_id: UUID
    connection_instance_id: ConnectionID
    session_id: ConnectionID
    policy_digest: Digest
    grant_digest: Digest
    policy_max_runs: Limit
    grant_max_runs: Limit
    server_policy_used: Count
    server_grant_used: Count
    device_policy_used: Count
    device_grant_used: Count
    remaining_runs: Count
    observed_at: datetime

    @model_validator(mode="after")
    def valid_observation(self) -> Self:
        for policy_used, grant_used in (
            (self.server_policy_used, self.server_grant_used),
            (self.device_policy_used, self.device_grant_used),
        ):
            ScriptBudgetWire(
                protocol_version=1,
                session_id=self.session_id,
                policy_digest=self.policy_digest,
                grant_digest=self.grant_digest,
                policy_max_runs=self.policy_max_runs,
                grant_max_runs=self.grant_max_runs,
                policy_used=policy_used,
                grant_used=grant_used,
            )
        remaining = min(
            self.policy_max_runs - self.server_policy_used,
            self.policy_max_runs - self.device_policy_used,
            self.grant_max_runs - self.server_grant_used,
            self.grant_max_runs - self.device_grant_used,
        )
        if (
            remaining != self.remaining_runs
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError("Invalid script budget observation")
        return self
