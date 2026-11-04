"""Configuration-only employee business bindings and owner metadata."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BusinessOperation = Literal[
    "order_query", "logistics_query", "customer_profile_query", "membership_query"
]


class ConnectionOwnerQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    requested_by: UUID
    connection_id: UUID


class ConnectionOperationConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal[
        "business_connection_configuration"
    ] = "business_connection_configuration"
    can_enable: Literal[False] = False
    operation: BusinessOperation
    report_id: UUID | None
    status: Literal[
        "not_tested", "running", "current", "stale", "not_passed", "unavailable"
    ]
    valid_until: datetime | None
    issues: list[
        Literal[
            "test_not_passed",
            "demo_only",
            "configuration_changed",
            "capability_removed",
            "superseded",
            "expired",
            "reviewer_unavailable",
            "credential_unavailable",
            "evidence_unknown",
        ]
    ]


class BusinessConnectionOwner(ConnectionOwnerQuery):
    scope: Literal[
        "business_connection_configuration"
    ] = "business_connection_configuration"
    can_enable: Literal[False] = False
    name: str
    mode: Literal["http", "demo"]
    enabled: bool
    operations: list[BusinessOperation]
    revision: int = Field(ge=1, strict=True)
    configuration_revision: int = Field(ge=1, strict=True)
    authorization_revision: int = Field(ge=1, strict=True)
    resource_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_status: Literal["not_tested", "passed", "unavailable"]
    configuration_tests: list[ConnectionOperationConfiguration]
    checked_at: datetime

    @model_validator(mode="after")
    def complete_operation_metadata(self) -> "BusinessConnectionOwner":
        expected = set(self.operations)
        if (
            len(expected) != len(self.operations)
            or len(self.configuration_tests) != len(expected)
            or {row.operation for row in self.configuration_tests} != expected
        ):
            raise ValueError("连接各操作的配置测试元数据不完整")
        for row in self.configuration_tests:
            if row.status == "current" and (
                row.report_id is None
                or row.valid_until is None
                or row.valid_until.tzinfo is None
                or self.checked_at.tzinfo is None
                or row.valid_until <= self.checked_at
                or row.issues
            ):
                raise ValueError("连接配置测试有效期或依据无法核验")
        return self


class AgentBusinessConnectionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    operations: list[BusinessOperation] = Field(min_length=1, max_length=4)
    enabled: bool = Field(default=True, strict=True)

    @field_validator("operations")
    @classmethod
    def unique_operations(
        cls, values: list[BusinessOperation]
    ) -> list[BusinessOperation]:
        if len(set(values)) != len(values):
            raise ValueError("查询操作不能重复")
        return sorted(values)


class AgentBusinessConnectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal[
        "employee_business_configuration"
    ] = "employee_business_configuration"
    can_enable: Literal[False] = False
    id: UUID
    project_id: UUID
    agent_id: UUID
    connection_id: UUID
    operations: list[BusinessOperation]
    enabled: bool
    revision: int = Field(ge=1, strict=True)
    configuration_revision: int = Field(ge=1, strict=True)
    authorization_revision: int = Field(ge=1, strict=True)
    resource_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    configured_by: UUID


class AgentBusinessConnections(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal[
        "employee_business_configuration"
    ] = "employee_business_configuration"
    can_enable: Literal[False] = False
    project_id: UUID
    agent_id: UUID
    bindings: list[AgentBusinessConnectionRecord]
