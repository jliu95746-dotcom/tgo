"""Binding validation never implies a passing test."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.knowledge import KnowledgeChannel
from app.schemas.scenario_resources import ResourceKind

ScenarioId = Literal[
    "knowledge",
    "logistics",
    "aftersales",
    "assistant",
    "files",
    "report",
    "screen",
]


class ScenarioModelBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: UUID
    model_id: str = Field(min_length=1, max_length=200)


class ScenarioBindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: ScenarioModelBinding | None = None
    knowledge_ids: list[UUID] = Field(default_factory=list, max_length=50)
    tool_ids: list[UUID] = Field(default_factory=list, max_length=50)
    workflow_ids: list[UUID] = Field(default_factory=list, max_length=50)
    connection_ids: list[UUID] = Field(default_factory=list, max_length=50)
    device_ids: list[UUID] = Field(default_factory=list, max_length=1)


class ScenarioReadinessRequest(BaseModel):
    template_id: ScenarioId
    channel: KnowledgeChannel = KnowledgeChannel.WEB
    bindings: ScenarioBindings = Field(default_factory=ScenarioBindings)


class ReadinessIssue(BaseModel):
    kind: ResourceKind
    code: Literal[
        "missing",
        "not_ready",
        "unavailable",
        "unverified",
        "not_supported",
        "invalid_binding",
        "capability_missing",
    ]
    message: str
    action: Literal["retry", "choose_resource", "configure_resource"]
    route: str


class ScenarioReadinessReport(BaseModel):
    project_id: UUID
    template_id: ScenarioId
    template_version: int = 1
    channel: KnowledgeChannel
    configuration_ready: bool
    test_status: Literal["not_tested"] = "not_tested"
    can_enable: Literal[False] = False
    effective_bindings: ScenarioBindings
    issues: list[ReadinessIssue]


class DeviceToolName(BaseModel):
    name: str


class DeviceToolNames(BaseModel):
    tools: list[DeviceToolName]
