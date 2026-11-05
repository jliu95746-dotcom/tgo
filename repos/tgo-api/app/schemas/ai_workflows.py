"""
AI Workflows (Workflow Engine proxy) schemas.

These schemas are aligned with `docs/api_workflow.json` (Workflow Engine
OpenAPI).
They are intentionally strict in typing (no Any/Dict in request/response
signatures),
while still being forward-compatible via BaseSchema(extra="ignore").
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional, Union
from uuid import UUID

from pydantic import (
    ConfigDict,
    Field,
    FiniteFloat,
    JsonValue,
    StrictBool,
    field_serializer,
    field_validator,
    model_validator,
)

from app.schemas.base import BaseSchema


# ---- Common / helper schemas ----
class KeyValue(BaseSchema):
    key: str = Field(..., description="Key")
    value: str = Field(..., description="Value")


class FormField(BaseSchema):
    key: str = Field(..., description="Field name")
    value: str = Field(..., description="Field value")
    type: Literal["text", "file"] = Field(
        default="text", description="Field type"
    )


class Category(BaseSchema):
    id: str = Field(..., description="Category ID")
    name: str = Field(..., description="Category name")
    description: str = Field(..., description="Category description")


class Position(BaseSchema):
    x: float
    y: float


class InputVariable(BaseSchema):
    name: str = Field(..., description="The name of the variable")
    type: Literal["string", "number", "boolean"] = Field(
        ..., description="The type of the variable"
    )
    description: Optional[str] = Field(
        default=None, description="The description of the variable"
    )
    required: StrictBool = Field(
        default=False, description="Whether the workflow input is required"
    )
    min_length: int | None = Field(default=None, ge=0, le=65535, strict=True)
    max_length: int | None = Field(default=None, ge=0, le=65535, strict=True)
    minimum: FiniteFloat | None = None
    maximum: FiniteFloat | None = None
    allowed_values: list[str] | None = Field(
        default=None, min_length=1, max_length=30
    )

    @field_validator("allowed_values", mode="before")
    @classmethod
    def reject_spaced_choices(cls, value: object) -> object:
        if isinstance(value, list) and any(
            isinstance(choice, str) and choice != choice.strip()
            for choice in value
        ):
            raise ValueError(
                "Allowed text values cannot have surrounding whitespace"
            )
        return value

    @model_validator(mode="after")
    def validate_constraints(self) -> "InputVariable":
        has_length = self.min_length is not None or self.max_length is not None
        has_range = self.minimum is not None or self.maximum is not None
        if (
            (has_length or self.allowed_values is not None)
            and self.type != "string"
        ) or (has_range and self.type != "number"):
            raise ValueError("Input constraints do not match variable type")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("Minimum text length exceeds maximum")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("Minimum number exceeds maximum")
        if self.allowed_values is not None and (
            len(set(self.allowed_values)) != len(self.allowed_values)
            or any(
                not value
                or len(value) > 100
                or (
                    self.min_length is not None
                    and len(value) < self.min_length
                )
                or (
                    self.max_length is not None
                    and len(value) > self.max_length
                )
                for value in self.allowed_values
            )
        ):
            raise ValueError(
                (
                    'Allowed text values must be unique, nonblank, and meet '
                    'length limits'
                )
            )
        return self


class OutputField(BaseSchema):
    key: str = Field(..., description="Key name of the output field")
    value: str = Field(
        ..., description="Value or variable reference of the output field"
    )


class PaginationInfo(BaseSchema):
    total: int = Field(..., description="Total number of items")
    limit: int = Field(..., description="Items per page")
    offset: int = Field(..., description="Offset (skip)")
    has_next: bool = Field(..., description="Whether there are more items")
    has_prev: bool = Field(..., description="Whether there are previous items")


# ---- Enums ----
ExecutionStatus = Literal[
    "pending",
    "running",
    "waiting_approval",
    "pending_resume",
    "completed",
    "failed",
    "rejected",
    "expired",
    "cancelled",
]
WorkflowStatus = Literal["draft", "active", "archived"]


# ---- Node data (discriminated union on `type`) ----


class InputNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ..., description="The unique reference key of the node"
    )
    type: Literal["input"] = Field(
        default="input", description="Node type: input"
    )
    input_variables: list[InputVariable] = Field(
        default_factory=list,
        description="Input variables defined for the conversation",
    )


class AnswerNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ..., description="The unique reference key of the node"
    )
    type: Literal["answer"] = Field(
        default="answer", description="Node type: answer"
    )
    output_type: Literal["variable", "template", "structured"] = Field(
        ..., description="Output configuration type"
    )
    output_variable: Optional[str] = Field(
        default=None,
        description="Selected output variable path when type is 'variable'",
    )
    output_template: Optional[str] = Field(
        default=None,
        description="Jinja2 template content when type is 'template'",
    )
    output_structure: Optional[list[OutputField]] = Field(
        default=None,
        description="Structured output fields when type is 'structured'",
    )


class TimerNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ..., description="The unique reference key of the node"
    )
    type: Literal["timer"] = Field(
        default="timer", description="Node type: timer"
    )
    cron_expression: str = Field(
        ...,
        min_length=1,
        max_length=512,
        strict=True,
        description="Numeric five-field schedule expression",
    )
    timezone: str = Field(
        default="UTC",
        min_length=1,
        max_length=100,
        strict=True,
        description="IANA timezone; legacy definitions use UTC",
    )


class WebhookNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ..., description="The unique reference key of the node"
    )
    type: Literal["webhook"] = Field(
        default="webhook", description="Node type: webhook"
    )
    path: Optional[str] = Field(
        default=None, description="Optional custom endpoint path suffix"
    )
    method: Literal["GET", "POST"] = Field(
        default="POST", description="HTTP method for the webhook"
    )


class EventNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ..., description="The unique reference key of the node"
    )
    type: Literal["event"] = Field(
        default="event", description="Node type: event"
    )
    event_type: str = Field(
        ..., description="Internal system event type identifier"
    )


class LLMNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ..., description="The unique reference key of the node"
    )
    type: Literal["llm"] = Field(default="llm", description="Node type: llm")
    provider_id: Optional[str] = Field(
        default=None, description="LLM provider ID"
    )
    model_id: Optional[str] = Field(default=None, description="Model ID")
    model_name: Optional[str] = Field(
        default=None, description="Model display name"
    )
    system_prompt: Optional[str] = Field(
        default=None, description="System prompt"
    )
    user_prompt: str = Field(
        ...,
        description=(
            'User prompt, supports {{node_key.var}} variable references'
        ),
    )
    temperature: float = Field(default=0.7, description="Sampling temperature")
    max_tokens: int = Field(
        default=2000, description="Maximum generation tokens"
    )
    tool_ids: list[str] = Field(
        default_factory=list, description="List of tool IDs"
    )
    collection_ids: list[str] = Field(
        default_factory=list, description="Knowledge base IDs"
    )


class AgentNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ..., description="The unique reference key of the node"
    )
    type: Literal["agent"] = Field(
        default="agent", description="Node type: agent"
    )
    agent_id: str = Field(..., description="Agent ID")
    agent_name: Optional[str] = Field(default=None, description="Agent name")
    input_mapping: Optional[dict[str, str]] = Field(
        default=None, description="Input parameters mapping"
    )


class ApprovalNodeData(BaseSchema):
    label: str
    reference_key: str
    type: Literal["approval"] = "approval"
    approver_staff_id: str
    summary: str = Field(min_length=1, max_length=500)
    expires_in_minutes: int = Field(default=1440, ge=1, le=10080)
    after_approval_action: Optional[Literal["create_aftersales_review"]] = None
    case_category: Optional[
        Literal[
            "return_request",
            "exchange_request",
            "quality_issue",
        ]
    ] = None

    @field_validator("approver_staff_id")
    @classmethod
    def valid_assignee(cls, value: str) -> str:
        UUID(value)
        return value

    @field_validator("summary")
    @classmethod
    def nonempty_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("审批摘要不能为空")
        return value.strip()


class ToolNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ..., description="The unique reference key of the node"
    )
    type: Literal["tool"] = Field(
        default="tool", description="Node type: tool"
    )
    tool_id: str = Field(..., description="Tool ID")
    tool_name: Optional[str] = Field(default=None, description="Tool name")
    config: Optional[dict[str, JsonValue]] = Field(
        default=None, description="Tool configuration parameters"
    )
    input_mapping: Optional[dict[str, str]] = Field(
        default=None, description="Input parameters mapping"
    )


class BusinessQueryNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ..., description="The unique reference key of the node"
    )
    type: Literal["business_query"] = "business_query"
    connection_id: UUID = Field(..., description="Owned business connection")
    operation: Literal["order_query", "logistics_query"]
    order_variable: Literal["input.order_no"] = "input.order_no"


class APINodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ..., description="The unique reference key of the node"
    )
    type: Literal["api"] = Field(default="api", description="Node type: api")
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = Field(
        ..., description="HTTP method"
    )
    url: str = Field(..., description="Request URL")
    headers: list[KeyValue] = Field(
        default_factory=list, description="HTTP headers"
    )
    params: list[KeyValue] = Field(
        default_factory=list, description="Query parameters"
    )
    body_type: Literal[
        "none", "json", "form-data", "x-www-form-urlencoded", "raw"
    ] = Field(..., description="Request body type")
    body: Optional[str] = Field(
        default=None, description="Request body content"
    )
    form_data: list[FormField] = Field(
        default_factory=list, description="Form data"
    )
    form_url_encoded: list[KeyValue] = Field(
        default_factory=list, description="URL-encoded form data"
    )
    raw_type: Optional[Literal["text", "html", "xml", "javascript"]] = Field(
        default=None, description="Raw text type"
    )


class ConditionNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ..., description="The unique reference key of the node"
    )
    type: Literal["condition"] = Field(
        default="condition", description="Node type: condition"
    )
    condition_type: Literal["expression", "variable", "llm"] = Field(
        ..., description="Condition evaluation type"
    )
    expression: Optional[str] = Field(
        default=None, description="Python expression"
    )
    variable: Optional[str] = Field(
        default=None, description="Referenced variable path"
    )
    operator: Optional[
        Literal[
            "equals",
            "notEquals",
            "contains",
            "greaterThan",
            "lessThan",
            "isEmpty",
            "isNotEmpty",
        ]
    ] = Field(default=None, description="Comparison operator")
    compare_value: Optional[str] = Field(
        default=None, description="Comparison value"
    )
    llm_prompt: Optional[str] = Field(
        default=None, description="LLM evaluation prompt"
    )
    provider_id: Optional[str] = Field(
        default=None, description="LLM provider ID"
    )
    model_id: Optional[str] = Field(default=None, description="Model ID")


class ClassifierNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ..., description="The unique reference key of the node"
    )
    type: Literal["classifier"] = Field(
        default="classifier", description="Node type: classifier"
    )
    input_variable: str = Field(
        ..., description="Input variable path to classify"
    )
    provider_id: Optional[str] = Field(
        default=None, description="LLM provider ID"
    )
    model_id: Optional[str] = Field(default=None, description="Model ID")
    categories: list[Category] = Field(
        ..., description="Defined classification rules"
    )


class ParallelNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(
        ..., description="The unique reference key of the node"
    )
    type: Literal["parallel"] = Field(
        default="parallel", description="Node type: parallel"
    )
    branches: int = Field(..., description="Number of branches")
    wait_for_all: bool = Field(
        default=True,
        description="Whether to wait for all branches to complete",
    )
    timeout: Optional[int] = Field(
        default=None, description="Execution timeout in seconds"
    )


NodeData = Annotated[
    Union[
        InputNodeData,
        AnswerNodeData,
        TimerNodeData,
        WebhookNodeData,
        EventNodeData,
        LLMNodeData,
        AgentNodeData,
        ApprovalNodeData,
        ToolNodeData,
        BusinessQueryNodeData,
        APINodeData,
        ConditionNodeData,
        ClassifierNodeData,
        ParallelNodeData,
    ],
    Field(discriminator="type"),
]


class WorkflowEdge(BaseSchema):
    id: str = Field(..., description="The unique identifier of the edge")
    source: str = Field(..., description="The source node ID")
    target: str = Field(..., description="The target node ID")
    source_handle: Optional[str] = Field(
        default=None, alias="sourceHandle", description="The source handle ID"
    )
    target_handle: Optional[str] = Field(
        default=None, alias="targetHandle", description="The target handle ID"
    )
    type: Optional[str] = Field(
        default="smoothstep", description="The type of the edge"
    )
    data: Optional[dict[str, JsonValue]] = Field(
        default=None, description="Additional data for the edge"
    )


class WorkflowNodeInput(BaseSchema):
    id: str
    type: str
    position: Position
    data: NodeData


class WorkflowNodeOutput(BaseSchema):
    id: str
    type: str
    position: Position
    data: NodeData


class WorkflowDefinition(BaseSchema):
    nodes: list[WorkflowNodeOutput] = Field(
        ..., description="List of workflow nodes"
    )
    edges: list[WorkflowEdge] = Field(
        ..., description="List of workflow edges"
    )


# ---- Workflow models ----
class WorkflowCreate(BaseSchema):
    name: str = Field(..., description="Workflow name")
    description: Optional[str] = Field(
        default=None, description="Detailed description of the workflow"
    )
    tags: list[str] = Field(
        default_factory=list, description="List of workflow tags"
    )
    nodes: list[WorkflowNodeInput] = Field(
        ..., description="List of workflow nodes"
    )
    edges: list[WorkflowEdge] = Field(
        ..., description="List of workflow edges"
    )


class WorkflowUpdate(BaseSchema):
    expected_version: int | None = Field(default=None, ge=1)
    name: Optional[str] = Field(default=None, description="Workflow name")
    description: Optional[str] = Field(
        default=None, description="Detailed description of the workflow"
    )
    nodes: Optional[list[WorkflowNodeInput]] = Field(
        default=None, description="List of workflow nodes"
    )
    edges: Optional[list[WorkflowEdge]] = Field(
        default=None, description="List of workflow edges"
    )
    status: Optional[WorkflowStatus] = Field(
        default=None, description="Workflow status"
    )
    tags: Optional[list[str]] = Field(
        default=None, description="List of workflow tags"
    )


class WorkflowDuplicateRequest(BaseSchema):
    name: Optional[str] = Field(
        default=None, description="New name for the duplicated workflow"
    )


class WorkflowValidateRequest(BaseSchema):
    nodes: list[WorkflowNodeInput] = Field(
        ..., description="List of workflow nodes to validate"
    )
    edges: list[WorkflowEdge] = Field(
        ..., description="List of workflow edges to validate"
    )


class WorkflowValidationResponse(BaseSchema):
    valid: bool = Field(
        ..., description="Whether the workflow passed validation"
    )
    errors: list[str] = Field(
        ..., description="List of validation error messages"
    )


class WorkflowVariable(BaseSchema):
    name: str = Field(..., description="Variable name")
    type: str = Field(
        ..., description="Variable type (string, number, boolean, object)"
    )
    description: Optional[str] = Field(
        default=None, description="Variable description"
    )


class NodeVariables(BaseSchema):
    node_id: str = Field(..., description="Node ID")
    reference_key: str = Field(..., description="Node reference key")
    node_type: str = Field(..., description="Node type")
    node_label: str = Field(..., description="Node label")
    outputs: list[WorkflowVariable] = Field(
        ..., description="Output variables of the node"
    )


class WorkflowVariablesResponse(BaseSchema):
    variables: list[NodeVariables] = Field(
        ..., description="List of available variables per node"
    )


class WorkflowSummary(BaseSchema):
    published_version: int | None = None
    publication_revision: int = 0
    name: str = Field(..., description="Workflow name")
    description: Optional[str] = Field(
        default=None, description="Detailed description of the workflow"
    )
    tags: list[str] = Field(
        default_factory=list, description="List of workflow tags"
    )
    id: str = Field(..., description="Unique identifier of the workflow")
    status: WorkflowStatus = Field(..., description="Current status")
    version: int = Field(..., description="Version number")
    updated_at: datetime = Field(..., description="Last update time")


class PaginatedWorkflowSummaryResponse(BaseSchema):
    data: list[WorkflowSummary] = Field(
        ..., description="List of items for current page"
    )
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class WorkflowInDB(BaseSchema):
    published_version: int | None = None
    publication_revision: int = 0
    name: str = Field(..., description="Workflow name")
    description: Optional[str] = Field(
        default=None, description="Detailed description of the workflow"
    )
    tags: list[str] = Field(
        default_factory=list, description="List of workflow tags"
    )
    id: str = Field(..., description="Unique identifier of the workflow")
    definition: WorkflowDefinition = Field(
        ..., description="Workflow graph definition"
    )
    status: WorkflowStatus = Field(..., description="Current status")
    version: int = Field(..., description="Version number")
    created_by: Optional[str] = Field(default=None, description="Creator ID")
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


# ---- Executions ----
class WorkflowCustomerMessageLookup(BaseSchema):
    """Find an original AI request within a server-owned customer session."""

    model_config = ConfigDict(extra="forbid")

    visitor_session_id: UUID
    source_message_id: str = Field(min_length=1, max_length=255)


class WorkflowChannelMessageLookup(BaseSchema):
    """Locate the server-owned AI request from the original chat message."""

    model_config = ConfigDict(extra="forbid")

    channel_id: str = Field(min_length=1, max_length=255)
    source_message_id: str = Field(min_length=1, max_length=255)


class WorkflowExecuteRequest(BaseSchema):
    release_version: int | None = Field(default=None, ge=1)
    expected_release_id: str | None = Field(default=None, min_length=1)
    expected_release_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    execution_mode: Literal["draft_test", "production"] = "draft_test"
    inputs: dict[str, JsonValue] = Field(
        default_factory=dict,
        description=(
            'Input variables passed to the start node, where Key is the '
            'variable name'
        ),
    )
    stream: bool = Field(
        default=False,
        description="Whether to stream the execution events using SSE",
    )
    async_mode: bool = Field(
        default=False,
        alias="async",
        description="Whether to execute asynchronously via Celery",
    )


class WorkflowSyncResponseMetadata(BaseSchema):
    duration: float
    start_time: datetime = Field(..., alias="startTime")
    end_time: datetime = Field(..., alias="endTime")


class WorkflowSyncResponse(BaseSchema):
    success: bool
    output: JsonValue
    metadata: WorkflowSyncResponseMetadata


def public_workflow_input_evidence(
    inputs: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    if inputs.get("capture_status") == "redacted":
        evidence: dict[str, JsonValue] = {"capture_status": "redacted"}
        field_count = inputs.get("field_count")
        if type(field_count) is int and 0 <= field_count <= 10000:
            evidence["field_count"] = field_count
        return evidence
    return {"capture_status": "redacted", "field_count": len(inputs)}


def public_workflow_node_output(
    node_type: str,
    output: JsonValue,
) -> JsonValue:
    if output is None or node_type == "answer":
        return output
    if not isinstance(output, dict):
        return {"capture_status": "redacted"}
    if node_type == "business_query":
        query_id = output.get("query_id")
        connection_id = output.get("connection_id")
        operation = output.get("operation")
        if (
            isinstance(query_id, str)
            and isinstance(connection_id, str)
            and isinstance(operation, str)
            and operation in {"order_query", "logistics_query"}
        ):
            try:
                return {
                    "query_id": str(UUID(query_id)),
                    "connection_id": str(UUID(connection_id)),
                    "operation": operation,
                }
            except ValueError:
                pass
        return public_workflow_input_evidence(output)
    if node_type == "condition" and type(output.get("result")) is bool:
        return {"result": output["result"]}
    if node_type == "approval" and output.get("requested") is True:
        return {"requested": True}
    evidence = public_workflow_input_evidence(output)
    agent_execution_id = output.get("agent_execution_id")
    if node_type == "agent" and isinstance(agent_execution_id, str):
        try:
            evidence["agent_execution_id"] = str(UUID(agent_execution_id))
        except ValueError:
            pass
    status_code = output.get("status_code")
    if node_type == "api" and type(status_code) is int:
        if 100 <= status_code <= 599:
            evidence["status_code"] = status_code
    return evidence


class NodeExecution(BaseSchema):
    node_id: str = Field(..., description="The node ID in the workflow")
    node_type: str = Field(..., description="Node type")
    status: ExecutionStatus = Field(..., description="Execution status")
    input: Optional[dict[str, JsonValue]] = Field(
        default=None, description="Input data for the node"
    )
    output: JsonValue = Field(
        default=None, description="Output results for the node"
    )
    error: Optional[str] = Field(
        default=None, description="Execution error message"
    )
    started_at: datetime = Field(..., description="Node execution start time")
    completed_at: Optional[datetime] = Field(
        default=None, description="Node execution completion time"
    )
    duration: Optional[int] = Field(
        default=None, description="Execution duration in milliseconds"
    )
    id: str = Field(
        ..., description="Unique identifier for the node execution record"
    )
    execution_id: str = Field(
        ..., description="The ID of the parent workflow execution record"
    )

    @field_serializer("input")
    def serialize_input(
        self,
        inputs: dict[str, JsonValue] | None,
    ) -> dict[str, JsonValue] | None:
        return (
            public_workflow_input_evidence(inputs)
            if inputs is not None
            else None
        )

    @field_serializer("output")
    def serialize_output(self, output: JsonValue) -> JsonValue:
        return public_workflow_node_output(self.node_type, output)


class WorkflowExecution(BaseSchema):
    release_id: str | None = None
    employee_agent_id: str | None = None
    definition_digest: str | None = None
    ai_request_id: str | None = None
    business_request_id: str | None = None
    visitor_session_id: str | None = None
    source_message_id: str | None = None
    source_channel: str | None = None
    parent_execution_id: str | None = None
    workflow_lineage: list[str] | None = None
    execution_mode: Literal["draft_test", "production"] | None = None
    workflow_version: int | None = None
    workflow_id: str = Field(..., description="Workflow ID")
    status: ExecutionStatus = Field(
        ..., description="Overall execution status"
    )
    input: Optional[dict[str, JsonValue]] = Field(
        default=None, description="Workflow startup input data"
    )
    output: JsonValue = Field(
        default=None, description="Final workflow output result"
    )
    error: Optional[str] = Field(
        default=None, description="Execution error message"
    )
    started_at: datetime = Field(
        ..., description="Workflow execution start time"
    )
    completed_at: Optional[datetime] = Field(
        default=None, description="Workflow execution completion time"
    )
    duration: Optional[int] = Field(
        default=None, description="Execution duration in milliseconds"
    )
    id: str = Field(
        ..., description="Unique identifier for the workflow execution record"
    )
    node_executions: list[NodeExecution] = Field(
        default_factory=list, description="List of detailed node executions"
    )

    @field_serializer("input")
    def serialize_input(
        self,
        inputs: dict[str, JsonValue] | None,
    ) -> dict[str, JsonValue] | None:
        return (
            public_workflow_input_evidence(inputs)
            if inputs is not None
            else None
        )


class WorkflowExecutionCancelResponse(BaseSchema):
    id: str = Field(..., description="Execution ID")
    status: ExecutionStatus = Field(..., description="Current status")
    cancelled_at: datetime = Field(..., description="Cancellation time")
