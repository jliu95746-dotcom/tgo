"""Secret-free setup materialization contracts shared with the AI owner."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from app.schemas.agent_activation import AgentActivationSelection


class ScenarioAgentSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class SetupToolBinding(BaseModel):
    tool_id: UUID
    enabled: bool = True


class SetupAgentSnapshot(BaseModel):
    name: str
    instruction: str
    model: str
    llm_provider_id: UUID | None = None
    is_active: Literal[False] = False
    is_default: Literal[False] = False
    config: dict[str, JsonValue]
    tools: list[SetupToolBinding]
    collections: list[str]
    workflows: list[str]
    bound_device_id: str | None = None
    skills_enabled: bool
    humanization_skill_name: str | None
    humanization_skill_enabled: bool


class ScenarioAgentSnapshot(BaseModel):
    revision: int = Field(ge=1)
    agent: SetupAgentSnapshot
    activation_selection: AgentActivationSelection | None = None


class ScenarioAgentReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    created_by: UUID
    draft_id: UUID
    revision: int = Field(ge=1)
    agent_id: UUID
    is_active: bool
    can_enable: Literal[False] = False
    activation_selection: AgentActivationSelection | None = None
