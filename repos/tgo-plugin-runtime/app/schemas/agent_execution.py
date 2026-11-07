"An immutable run identity is a comparison input, never an execution \
grant."
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.knowledge import KnowledgeChannel


class AgentExecutionReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: UUID
    agent_id: UUID
    channel: KnowledgeChannel | None
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    business_evidence_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class AgentExecutionCheckReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal["employee_execution_check"] = "employee_execution_check"
    can_enable: Literal[False] = False
    reference: AgentExecutionReference
    checked_at: datetime

    @model_validator(mode="after")
    def explicit_time(self) -> "AgentExecutionCheckReceipt":
        if self.checked_at.tzinfo is None:
            raise ValueError("Execution check requires an explicit time")
        return self
