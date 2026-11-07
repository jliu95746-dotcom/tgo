"""HTTP trial contracts synchronized with the owning AI service."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.knowledge import KnowledgeChannel


class AgentTrialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    message: str = Field(min_length=1, max_length=4000)
    channel: KnowledgeChannel = KnowledgeChannel.WEB
    timeout: int = Field(default=45, ge=5, le=60)

    @field_validator("message")
    @classmethod
    def question_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写测试问题")
        return value


class AgentTrialReceipt(BaseModel):
    """Private quota reference; no customer message or question is fabricated."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["agent_trial"] = "agent_trial"
    staff_id: UUID
    agent_id: UUID
    request_key: UUID
    request_digest: str = Field(min_length=64, max_length=64)
    report_id: UUID | None = None


class TrialKnowledgeSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection_id: UUID
    document_id: UUID
    file_id: UUID | None = None
    content_digest: str = Field(min_length=64, max_length=64)
    relevance_score: float


class TrialKnowledgeRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection_id: UUID
    resource_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class TrialKnowledgeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    channel: KnowledgeChannel
    binding_mode: Literal["project_default", "explicit", "unbound"]
    collections: list[TrialKnowledgeRevision]
    issues: list[str] = Field(default_factory=list)
    captured_at: datetime
    verification: Literal[
        "pending", "consistent", "changed", "unknown"
    ] = "pending"


class TrialApplicability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal[
        "agent_configuration_and_knowledge"
    ] = "agent_configuration_and_knowledge"
    status: Literal[
        "not_recorded", "not_checked", "current", "changed", "unknown"
    ] = "not_recorded"
    checked_at: datetime | None = None
    issues: list[
        Literal[
            "trial_not_completed",
            "snapshot_not_recorded",
            "changed_during_trial",
            "configuration_changed",
            "knowledge_changed",
            "knowledge_unavailable",
            "agent_unavailable",
            "revision_unconfirmed",
        ]
    ] = Field(default_factory=list)


class AgentTrialReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    agent_id: UUID
    request_key: UUID
    mode: Literal["knowledge_grounded"] = "knowledge_grounded"
    status: Literal["running", "completed", "blocked", "failed", "cancelled"]
    assessment: Literal["not_reviewed"] = "not_reviewed"
    can_enable: Literal[False] = False
    channel: KnowledgeChannel
    question: str
    answer: str = ""
    configuration_fingerprint: str | None = None
    knowledge_snapshot: TrialKnowledgeSnapshot | None = None
    applicability: TrialApplicability = Field(
        default_factory=TrialApplicability
    )
    knowledge_status: Literal[
        "matched", "no_match", "unavailable", "over_budget", "skipped"
    ] | None = None
    sources: list[TrialKnowledgeSource] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
