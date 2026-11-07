"""Metadata-only original request control, including preparation and tombstones."""

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
from app.schemas.agent_business_trials import BusinessTrialReport, Operation

PreparationError = Literal[
    "preparation_timeout", "preparation_failed", "interrupted", "cancelled"
]


class BusinessEmployeeCurrentState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: Literal[
        "employee_business_current_state"
    ] = "employee_business_current_state"
    can_enable: Literal[False] = False
    project_id: UUID
    actor_id: UUID
    agent_id: UUID
    active: bool = Field(strict=True)
    checked_at: datetime

    @field_validator("checked_at")
    @classmethod
    def explicit_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("员工状态核验缺少时区")
        return value


class BusinessRequestControl(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    connection_id: UUID
    operation: Operation


class BusinessRequestState(BusinessRequestControl):
    scope: Literal[
        "employee_business_trial_request"
    ] = "employee_business_trial_request"
    can_enable: Literal[False] = False
    project_id: UUID
    actor_id: UUID
    agent_id: UUID
    status: Literal["preparing", "ready", "failed", "cancelled"]
    created_at: datetime
    deadline_at: datetime
    completed_at: datetime | None
    error_code: PreparationError | None
    report: BusinessTrialReport | None

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
        if self.status == "ready":
            if (
                self.report is None
                or self.completed_at is None
                or self.error_code is not None
            ):
                raise ValueError("请求缺少原业务报告")
            if any(
                getattr(self.report, key) != getattr(self, key)
                for key in (
                    "project_id",
                    "actor_id",
                    "agent_id",
                    "request_id",
                    "connection_id",
                    "operation",
                )
            ):
                raise ValueError("请求和业务报告范围不一致")
        elif self.status == "preparing":
            if (
                self.report is not None
                or self.completed_at is not None
                or self.error_code is not None
            ):
                raise ValueError("准备状态与请求结果不一致")
        elif (
            self.report is not None
            or self.completed_at is None
            or self.error_code is None
        ):
            raise ValueError("准备终态缺少结果分类")
        if (self.status == "cancelled") != (self.error_code == "cancelled"):
            raise ValueError("取消结果与请求状态不一致")
        return self
