"""Safe projection for the G3 historical intent evaluation JSONL contract."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


IntentNameV1 = Literal[
    "product_inquiry",
    "pricing_promotion",
    "order_assistance",
    "order_query",
    "logistics_query",
    "payment_issue",
    "after_sales_issue",
    "refund_return_inquiry",
    "complaint",
    "sales_lead",
    "human_handoff",
    "unknown",
]
IntentRoute = Literal[
    "auto_reply", "read_only_tool", "clarify", "human_handoff"
]


class IntentEvaluationExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_id: UUID
    analysis_mode: Literal["active", "shadow"]
    case_ids: tuple[UUID, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_unique_cases(self) -> "IntentEvaluationExportRequest":
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("case_ids 不能重复")
        return self


class IntentEvaluationPrediction(BaseModel):
    """Exactly the fields accepted by tgo-ai's ObservedPrediction."""

    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    intent: IntentNameV1
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    recommended_route: IntentRoute
    need_human: bool
    taxonomy_version: Literal["v1"]
    classifier_version: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$"
    )
    policy_version: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$"
    )

    @model_validator(mode="after")
    def require_consistent_handoff(self) -> "IntentEvaluationPrediction":
        if self.need_human != (self.recommended_route == "human_handoff"):
            raise ValueError("转人工标记与路由不一致")
        return self
