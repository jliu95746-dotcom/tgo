"""Server-owned provenance; never accepted as model or public route input."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.agent_execution import AgentExecutionReference


class ScriptRunSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_execution_id: UUID
    reference: AgentExecutionReference
    workflow_execution_id: UUID | None = None

    @model_validator(mode="after")
    def confirmed_channel(self) -> "ScriptRunSource":
        if self.reference.channel is None:
            raise ValueError("Runtime scripts require a confirmed channel")
        return self


class ScriptRuntimeApproval(BaseModel):
    """A server-selected approval; it is not an LLM function argument."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    actor_id: UUID
    report_id: UUID
    review_id: UUID
    selection_id: UUID
    selection_revision: int = Field(strict=True, ge=1, le=2147483647)
    selection_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
