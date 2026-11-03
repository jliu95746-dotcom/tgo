"""Versioned templates and credential-free setup drafts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.knowledge import KnowledgeChannel
from app.schemas.scenario_readiness import ScenarioBindings, ScenarioId
from app.schemas.scenario_resources import ResourceKind

ScenarioModule = Literal["agents", "workflows", "devices"]
ScenarioStep = Literal[
    "scenario", "business", "resources", "testing", "activation"
]


class ScenarioBusiness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=255)
    profession: str = Field(default="", max_length=255)
    company_summary: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=6000)


class ScenarioAgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    markdown: bool = True
    add_datetime_to_context: bool = True
    skills_enabled: bool = True
    show_tool_calls: bool = False
    tool_call_limit: int = Field(default=10, ge=1, le=100)
    num_history_runs: int = Field(default=5, ge=0, le=100)
    humanization_skill_name: str | None = Field(default=None, max_length=200)
    humanization_skill_enabled: bool = False


class ScenarioTemplate(BaseModel):
    id: ScenarioId
    module: ScenarioModule
    version: int = Field(ge=1)
    title: str
    available: bool
    default_model_mode: Literal["system_default", "not_required"]
    required_resources: list[ResourceKind]
    required_capabilities: list[str]
    business_defaults: ScenarioBusiness
    test_questions: list[str]
    recommended_workflows: list[str]


class ScenarioDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID
    template_id: ScenarioId
    module: ScenarioModule
    template_version: int = Field(default=1, ge=1)
    channel: KnowledgeChannel = KnowledgeChannel.WEB
    business: ScenarioBusiness = Field(default_factory=ScenarioBusiness)
    bindings: ScenarioBindings = Field(default_factory=ScenarioBindings)
    agent_settings: ScenarioAgentSettings = Field(
        default_factory=ScenarioAgentSettings
    )


class ScenarioDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    step: ScenarioStep | None = None
    channel: KnowledgeChannel | None = None
    business: ScenarioBusiness | None = None
    bindings: ScenarioBindings | None = None
    agent_settings: ScenarioAgentSettings | None = None

    @model_validator(mode="after")
    def require_changes(self) -> "ScenarioDraftUpdate":
        if all(
            getattr(self, key) is None
            for key in (
                "step",
                "channel",
                "business",
                "bindings",
                "agent_settings",
            )
        ):
            raise ValueError("请提供需要保存的配置。")
        return self


class ScenarioDraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    created_by: UUID
    template_id: ScenarioId
    module: ScenarioModule
    template_version: int
    channel: KnowledgeChannel
    revision: int
    step: ScenarioStep
    status: Literal["draft"] = "draft"
    business: ScenarioBusiness
    bindings: ScenarioBindings
    agent_settings: ScenarioAgentSettings
    created_at: datetime
    updated_at: datetime
