"""Private workflow billing coordinates; never include service credentials."""

from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from app.schemas.agent_execution import AgentExecutionReference


class WorkflowUsageOwner(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    staff_id: UUID | None = None
    staff_token_version: int | None = Field(default=None, ge=1)
    reservation_id: UUID | None = None
    lease_id: UUID | None = None
    unmetered: Literal[True] | None = None

    @model_validator(mode="after")
    def require_one_complete_owner(self) -> "WorkflowUsageOwner":
        staff = (
            self.staff_id is not None and self.staff_token_version is not None
        )
        inherited = (
            self.reservation_id is not None and self.lease_id is not None
        )
        if sum((staff, inherited, self.unmetered is True)) != 1 or (
            (self.staff_id is None) != (self.staff_token_version is None)
            or (self.reservation_id is None) != (self.lease_id is None)
        ):
            raise ValueError("Workflow requires one complete billing owner")
        return self


class WorkflowAgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: UUID
    message: str = Field(min_length=1, max_length=10000)
    stream: Literal[False] = False
    knowledge_channel: Literal["internal"] = "internal"
    require_current_knowledge: Literal[True] = True
    session_id: str | None = Field(default=None, max_length=255)
    expected_device_id: UUID | None = None
    expected_device_policy_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def require_device_pair(self) -> "WorkflowAgentInput":
        if (self.expected_device_id is None) != (
            self.expected_device_policy_digest is None
        ):
            raise ValueError(
                "Device reference requires an exact policy digest"
            )
        return self


class WorkflowAICommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: UUID
    execution_id: UUID
    node_id: str = Field(min_length=1, max_length=128)
    payload: WorkflowAgentInput


class WorkflowAIContext(BaseModel):
    """Coordinates read from Workflow rather than a public request body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: UUID
    execution_id: UUID
    node_id: str = Field(min_length=1, max_length=128)
    agent_id: UUID
    owner: WorkflowUsageOwner
    status: Literal["running"]
    workflow_lineage: tuple[str, ...] = Field(default=(), max_length=4)
    expected_device_id: UUID | None = None
    expected_device_policy_digest: str | None = None


class WorkflowAIResult(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    success: Literal[True]
    content: str = Field(min_length=1, max_length=100000)
    execution_id: UUID
    execution_reference: AgentExecutionReference | None = None

    @field_validator("success", mode="before")
    @classmethod
    def require_true_boolean(cls, value: object) -> object:
        if value is not True:
            raise ValueError("AI success must be the boolean true")
        return value

    @model_validator(mode="after")
    def require_answer(self) -> "WorkflowAIResult":
        if not self.content.strip():
            raise ValueError("AI result requires an answer")
        return self


class WorkflowAICallReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: UUID
    node_id: str
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["claimed", "completed", "rejected", "unknown"]
    agent_execution_id: UUID | None = None

    @model_validator(mode="after")
    def require_complete_result(self) -> "WorkflowAICallReceipt":
        if (self.state == "completed") != (
            self.agent_execution_id is not None
        ):
            raise ValueError("Only completed calls have a result")
        return self


class WorkflowUsageReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["workflow"] = "workflow"
    execution_id: UUID
    staff_id: UUID
    staff_token_version: int = Field(ge=1)


class WorkflowUsageOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: UUID
    execution_id: UUID
    status: Literal[
        "pending",
        "running",
        "waiting_approval",
        "pending_resume",
        "completed",
        "failed",
        "rejected",
        "expired",
        "cancelled",
    ]
    output: JsonValue | None = None
    answer_available: bool = False


class WorkflowAICacheCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    command: WorkflowAICommand
    reservation_id: UUID | None = None
    lease_id: UUID | None = None

    @model_validator(mode="after")
    def require_complete_permit(self) -> "WorkflowAICacheCommand":
        if (self.reservation_id is None) != (self.lease_id is None):
            raise ValueError("Saved results require a complete quota permit")
        return self


class WorkflowAICacheWrite(WorkflowAICacheCommand):
    claim_id: UUID
    result: WorkflowAIResult

    @model_validator(mode="after")
    def require_original_producer(self) -> "WorkflowAICacheWrite":
        ref = self.result.execution_reference
        if (
            ref is None
            or ref.project_id != self.command.project_id
            or ref.agent_id != self.command.payload.agent_id
            or ref.channel != "internal"
        ):
            raise ValueError("Saved answers require their exact producer")
        return self


class WorkflowAICacheReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state: Literal["claimed", "pending", "completed"]
    claim_id: UUID
    result: WorkflowAIResult | None = None

    @model_validator(mode="after")
    def require_completed_result(self) -> "WorkflowAICacheReceipt":
        if (self.state == "completed") != (self.result is not None):
            raise ValueError("Only completed calls can return an answer")
        return self
