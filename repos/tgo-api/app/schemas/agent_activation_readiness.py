"""Current employee activation coverage; this read never changes activation."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.agent_activation import (
    AgentActivationSelection,
    ActivationResourceKind,
)
from app.schemas.agent_knowledge_capabilities import KnowledgeCaseStatus
from app.schemas.knowledge import KnowledgeChannel
from app.schemas.agent_business_reviews import BusinessOperationCapability


class ActivationBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal[
        "policy_missing",
        "requirements_changed",
        "owner_unavailable",
        "configuration_unavailable",
        "dependency_not_verified",
        "knowledge_not_ready",
        "tests_not_current",
        "script_capacity_exhausted",
    ]
    channel: KnowledgeChannel | None = None
    resource: ActivationResourceKind | None = None
    message: str = Field(min_length=1, max_length=250)


class ActivationChannelCoverage(AgentActivationSelection):
    policy_revision: int = Field(ge=1, strict=True)
    requirements_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: KnowledgeCaseStatus
    valid_until: datetime | None = None
    business_capabilities: list[BusinessOperationCapability] = Field(
        default_factory=list, max_length=200
    )


class AgentActivationReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    agent_id: UUID
    checked_by: UUID
    checked_at: datetime
    scope: Literal["employee_activation"] = "employee_activation"
    enabled: bool
    can_enable: bool
    configuration_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    model_owner_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    business_evidence_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    blockers: list[ActivationBlocker] = Field(
        default_factory=list, max_length=50
    )
    channels: list[ActivationChannelCoverage] = Field(
        default_factory=list, max_length=5
    )

    @model_validator(mode="after")
    def complete_current_coverage(self) -> "AgentActivationReadiness":
        if len({row.channel for row in self.channels}) != len(self.channels):
            raise ValueError("Duplicate activation channel")
        if self.checked_at.tzinfo is None:
            raise ValueError("Activation check requires timezone")
        if self.can_enable and (
            self.blockers
            or not self.configuration_fingerprint
            or not self.model_owner_revision
            or not self.channels
            or any(
                row.status != "passed"
                or row.valid_until is None
                or row.valid_until.tzinfo is None
                or row.valid_until <= self.checked_at
                for row in self.channels
            )
        ):
            raise ValueError("Incomplete current activation evidence")
        for row in self.channels:
            if (
                row.status == "passed"
                and row.template_id == "logistics"
                and (
                    not self.business_evidence_revision
                    or not {"order_query", "logistics_query"}.issubset(
                        {item.operation for item in row.business_capabilities}
                    )
                    or any(
                        item.status != "passed"
                        or item.report_id is None
                        or item.review_id is None
                        or item.issues
                        or item.valid_until is None
                        or item.valid_until.tzinfo is None
                        or item.valid_until <= self.checked_at
                        for item in row.business_capabilities
                    )
                )
            ):
                raise ValueError(
                    "Incomplete current business activation evidence"
                )
        return self
