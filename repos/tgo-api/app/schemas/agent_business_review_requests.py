"""Recoverable review intentions contain closed business choices, never secrets."""

from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from app.schemas.agent_business_reviews import (
    BusinessReviewRecord,
    BusinessReviewRequest,
)

ReviewRequestError = Literal[
    "review_timeout", "review_failed", "interrupted", "cancelled"
]


class BusinessReviewRequestState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal[
        "employee_business_review_request"
    ] = "employee_business_review_request"
    can_enable: Literal[False] = False
    project_id: UUID
    actor_id: UUID
    agent_id: UUID
    report_id: UUID
    request_id: UUID
    status: Literal["pending", "completed", "failed", "cancelled"]
    created_at: datetime
    deadline_at: datetime
    completed_at: datetime | None
    checked_at: datetime
    can_resume: bool = Field(strict=True)
    processing: bool = Field(strict=True)
    error_code: ReviewRequestError | None
    command: BusinessReviewRequest | None
    result: BusinessReviewRecord | None

    @field_validator("created_at", "deadline_at", "completed_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        return (
            value.replace(tzinfo=UTC)
            if value is not None and value.tzinfo is None
            else value
        )

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.processing and self.status != "pending":
            raise ValueError("评审终态不能仍在处理中")
        if self.checked_at.tzinfo is None or self.can_resume != (
            self.status == "pending"
            and not self.processing
            and self.command is not None
            and self.deadline_at > self.checked_at
        ):
            raise ValueError("评审续办状态缺少当前核验")
        if (
            self.command is not None
            and self.command.request_id != self.request_id
        ):
            raise ValueError("原评审命令编号不一致")
        if self.status == "pending":
            if (
                self.command is None
                or self.result is not None
                or self.completed_at is not None
                or self.error_code is not None
            ):
                raise ValueError("评审处理中状态与结果不一致")
        elif self.status == "completed":
            if (
                self.result is None
                or self.completed_at is None
                or self.error_code is not None
            ):
                raise ValueError("原评审缺少完整回执")
            expected = {
                "project_id": self.project_id,
                "reviewed_by": self.actor_id,
                "agent_id": self.agent_id,
                "report_id": self.report_id,
                "request_id": self.request_id,
            }
            if any(
                getattr(self.result, key) != value
                for key, value in expected.items()
            ):
                raise ValueError("原评审回执范围不一致")
            if self.command is not None and any(
                getattr(self.result, key) != getattr(self.command, key)
                for key in ("decision", "checks", "reason")
            ):
                raise ValueError("原评审命令与回执不一致")
        elif (
            self.result is not None
            or self.completed_at is None
            or self.error_code is None
        ):
            raise ValueError("评审终态缺少结果分类")
        if (self.status == "cancelled") != (self.error_code == "cancelled"):
            raise ValueError("取消状态与结果分类不一致")
        return self
