"""Owner-defined activation requirements; this contract grants no enablement."""

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.agent_trial_reviews import KnowledgeReviewCase
from app.schemas.knowledge import KnowledgeChannel

AgentTemplateId = Literal["knowledge", "aftersales", "logistics", "assistant"]
ActivationResourceKind = Literal[
    "model", "knowledge", "connection", "tool", "workflow", "device"
]
ActivationCapability = Literal[
    "governed_knowledge_search",
    "scoped_order_query",
    "scoped_logistics_query",
    "business_data_access",
]


class AgentActivationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: AgentTemplateId
    template_version: int = Field(ge=1, strict=True)
    channel: KnowledgeChannel


class AgentActivationRequirementsQuery(AgentActivationSelection):
    project_id: UUID
    requested_by: UUID
    agent_id: UUID | None = None


class AgentActivationRequirements(AgentActivationRequirementsQuery):
    scope: Literal["template_requirements"] = "template_requirements"
    can_enable: Literal[False] = False
    required_resources: list[ActivationResourceKind] = Field(
        min_length=1, max_length=10
    )
    required_capabilities: list[ActivationCapability] = Field(
        min_length=1, max_length=30
    )
    knowledge_cases: list[KnowledgeReviewCase] = Field(max_length=30)
    requirements_revision: str = Field(pattern=r"^[0-9a-f]{64}$")

    def definition_revision(self) -> str:
        definition = self.model_dump(
            mode="json",
            include={
                "template_id",
                "template_version",
                "channel",
                "required_resources",
                "required_capabilities",
                "knowledge_cases",
            },
        )
        for case in definition["knowledge_cases"]:
            if case.get("valid_from") is None:
                case.pop("valid_from", None)
        encoded = json.dumps(
            definition,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @model_validator(mode="after")
    def complete_definition(self) -> "AgentActivationRequirements":
        if len(set(self.required_resources)) != len(
            self.required_resources
        ) or len(set(self.required_capabilities)) != len(
            self.required_capabilities
        ):
            raise ValueError("Duplicate activation requirement")
        if len({case.id for case in self.knowledge_cases}) != len(
            self.knowledge_cases
        ):
            raise ValueError("Duplicate activation case")
        if any(
            case.template_id != self.template_id
            or case.template_version != self.template_version
            for case in self.knowledge_cases
        ):
            raise ValueError("Activation case does not match the template")
        if self.template_id in {"knowledge", "aftersales"}:
            expected = {case.expected_result for case in self.knowledge_cases}
            if (
                expected != {"grounded_answer", "safe_refusal"}
                or "knowledge" not in self.required_resources
            ):
                raise ValueError("Incomplete knowledge template requirements")
        elif self.knowledge_cases:
            raise ValueError(
                "Business template cannot use knowledge-only evidence"
            )
        if self.definition_revision() != self.requirements_revision:
            raise ValueError("Activation requirement revision mismatch")
        return self


class AgentActivationPolicyChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    selection: AgentActivationSelection


class AgentActivationPolicyRecord(AgentActivationSelection):
    id: UUID
    revision: int = Field(ge=1, strict=True)
    requirements_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    configured_by: UUID
    enabled: bool


class AgentActivationPolicies(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    agent_id: UUID
    policies: list[AgentActivationPolicyRecord] = Field(max_length=5)
    can_enable: Literal[False] = False

    @model_validator(mode="after")
    def unique_channels(self) -> "AgentActivationPolicies":
        if len({policy.channel for policy in self.policies}) != len(
            self.policies
        ):
            raise ValueError("Duplicate employee policy channel")
        return self
