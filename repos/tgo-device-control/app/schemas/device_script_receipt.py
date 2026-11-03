"""Metadata can remain queryable while result bodies are unavailable."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.device_script_execution import BusinessValue, Digest

StoredScriptOutcome = Literal[
    "unknown",
    "succeeded",
    "failed",
    "canceled",
    "timed_out",
    "output_exceeded",
    "rejected",
    "start_failed",
]


class ScriptExecutionRead(BaseModel):
    device_id: UUID
    execution_id: UUID
    session_id: UUID
    agent_id: UUID
    workflow_execution_id: UUID | None = None
    policy_digest: Digest
    parameters_digest: Digest
    context_digest: Digest
    status: StoredScriptOutcome
    failure: str = Field(max_length=64)
    created_at: datetime
    finished_at: datetime | None = None
    result_available: bool = False
    fields: dict[str, BusinessValue] = Field(
        default_factory=dict, max_length=32
    )

    @model_validator(mode="after")
    def expose_only_confirmed_fields(self) -> Self:
        if (
            bool(self.fields) != self.result_available
            or self.result_available
            and self.status != "succeeded"
        ):
            raise ValueError(
                "Unconfirmed receipt must not contain business fields"
            )
        return self
