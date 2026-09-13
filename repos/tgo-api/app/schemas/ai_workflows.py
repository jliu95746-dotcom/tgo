from __future__ import annotations

"""AI Workflows (Workflow Engine proxy) schemas.

These schemas are aligned with `docs/api_workflow.json` (Workflow Engine OpenAPI).
They are intentionally strict in typing (no Any/Dict in request/response signatures),
while still being forward-compatible via BaseSchema(extra="ignore").
"""

from datetime import datetime
from typing import Annotated, Literal, Optional, Union

from pydantic import Field, JsonValue

from app.schemas.base import BaseSchema


# ---- Common / helper schemas ----
class KeyValue(BaseSchema):
    key: str = Field(..., description="Key")
    value: str = Field(..., description="Value")


class FormField(BaseSchema):
    key: str = Field(..., description="Field name")
    value: str = Field(..., description="Field value")
    type: Literal["text", "file"] = Field("text", description="Field type")


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
    description: Optional[str] = Field(None, description="The description of the variable")


class OutputField(BaseSchema):
    key: str = Field(..., description="Key name of the output field")
    value: str = Field(..., description="Value or variable reference of the output field")


class PaginationInfo(BaseSchema):
    total: int = Field(..., description="Total number of items")
    limit: int = Field(..., description="Items per page")
    offset: int = Field(..., description="Offset (skip)")
    has_next: bool = Field(..., description="Whether there are more items")
    has_prev: bool = Field(..., description="Whether there are previous items")


# ---- Enums ----
ExecutionStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
WorkflowStatus = Literal["draft", "active", "archived"]


# ---- Node data (discriminated union on `type`) ----

class InputNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(..., description="The unique reference key of the node")
    type: Literal["input"] = Field("input", description="Node type: input")
    input_variables: list[InputVariable] = Field(
        default_factory=list, description="Input variables defined for the conversation"
    )


class AnswerNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(..., description="The unique reference key of the node")
    type: Literal["answer"] = Field("answer", description="Node type: answer")
    output_type: Literal["variable", "template", "structured"] = Field(
        ..., description="Output configuration type"
    )
    output_variable: Optional[str] = Field(
        None, description="Selected output variable path when type is 'variable'"
    )
    output_template: Optional[str] = Field(
        None, description="Jinja2 template content when type is 'template'"
    )
    output_structure: Optional[list[OutputField]] = Field(
        None, description="Structured output fields when type is 'structured'"
    )


class TimerNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(..., description="The unique reference key of the node")
    type: Literal["timer"] = Field("timer", description="Node type: timer")
    cron_expression: str = Field(..., description="Cron expression for scheduled tasks")


class WebhookNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(..., description="The unique reference key of the node")
    type: Literal["webhook"] = Field("webhook", description="Node type: webhook")
    path: Optional[str] = Field(None, description="Optional custom endpoint path suffix")
    method: Literal["GET", "POST"] = Field("POST", description="HTTP method for the webhook")


class EventNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(..., description="The unique reference key of the node")
    type: Literal["event"] = Field("event", description="Node type: event")
    event_type: str = Field(..., description="Internal system event type identifier")


class LLMNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(..., description="The unique reference key of the node")
    type: Literal["llm"] = Field("llm", description="Node type: llm")
    provider_id: Optional[str] = Field(None, description="LLM provider ID")
    model_id: Optional[str] = Field(None, description="Model ID")
    model_name: Optional[str] = Field(None, description="Model display name")
    system_prompt: Optional[str] = Field(None, description="System prompt")
    user_prompt: str = Field(
        ..., description="User prompt, supports {{node_key.var}} variable references"
    )
    temperature: float = Field(0.7, description="Sampling temperature")
    max_tokens: int = Field(2000, description="Maximum generation tokens")
    tool_ids: list[str] = Field(default_factory=list, description="List of tool IDs")
    collection_ids: list[str] = Field(default_factory=list, description="Knowledge base IDs")


class AgentNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(..., description="The unique reference key of the node")
    type: Literal["agent"] = Field("agent", description="Node type: agent")
    agent_id: str = Field(..., description="Agent ID")
    agent_name: Optional[str] = Field(None, description="Agent name")
    input_mapping: Optional[dict[str, str]] = Field(None, description="Input parameters mapping")


class ToolNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(..., description="The unique reference key of the node")
    type: Literal["tool"] = Field("tool", description="Node type: tool")
    tool_id: str = Field(..., description="Tool ID")
    tool_name: Optional[str] = Field(None, description="Tool name")
    config: Optional[dict[str, JsonValue]] = Field(None, description="Tool configuration parameters")
    input_mapping: Optional[dict[str, str]] = Field(None, description="Input parameters mapping")


class APINodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(..., description="The unique reference key of the node")
    type: Literal["api"] = Field("api", description="Node type: api")
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = Field(..., description="HTTP method")
    url: str = Field(..., description="Request URL")
    headers: list[KeyValue] = Field(default_factory=list, description="HTTP headers")
    params: list[KeyValue] = Field(default_factory=list, description="Query parameters")
    body_type: Literal["none", "json", "form-data", "x-www-form-urlencoded", "raw"] = Field(
        ..., description="Request body type"
    )
    body: Optional[str] = Field(None, description="Request body content")
    form_data: list[FormField] = Field(default_factory=list, description="Form data")
    form_url_encoded: list[KeyValue] = Field(default_factory=list, description="URL-encoded form data")
    raw_type: Optional[Literal["text", "html", "xml", "javascript"]] = Field(
        None, description="Raw text type"
    )


class ConditionNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(..., description="The unique reference key of the node")
    type: Literal["condition"] = Field("condition", description="Node type: condition")
    condition_type: Literal["expression", "variable", "llm"] = Field(
        ..., description="Condition evaluation type"
    )
    expression: Optional[str] = Field(None, description="Python expression")
    variable: Optional[str] = Field(None, description="Referenced variable path")
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
    ] = Field(None, description="Comparison operator")
    compare_value: Optional[str] = Field(None, description="Comparison value")
    llm_prompt: Optional[str] = Field(None, description="LLM evaluation prompt")
    provider_id: Optional[str] = Field(None, description="LLM provider ID")
    model_id: Optional[str] = Field(None, description="Model ID")


class ClassifierNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(..., description="The unique reference key of the node")
    type: Literal["classifier"] = Field("classifier", description="Node type: classifier")
    input_variable: str = Field(..., description="Input variable path to classify")
    provider_id: Optional[str] = Field(None, description="LLM provider ID")
    model_id: Optional[str] = Field(None, description="Model ID")
    categories: list[Category] = Field(..., description="Defined classification rules")


class ParallelNodeData(BaseSchema):
    label: str = Field(..., description="The display name of the node")
    reference_key: str = Field(..., description="The unique reference key of the node")
    type: Literal["parallel"] = Field("parallel", description="Node type: parallel")
    branches: int = Field(..., description="Number of branches")
    wait_for_all: bool = Field(True, description="Whether to wait for all branches to complete")
    timeout: Optional[int] = Field(None, description="Execution timeout in seconds")


NodeData = Annotated[
    Union[
        InputNodeData,
        AnswerNodeData,
        TimerNodeData,
        WebhookNodeData,
        EventNodeData,
        LLMNodeData,
        AgentNodeData,
        ToolNodeData,
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
    source_handle: Optional[str] = Field(None, alias="sourceHandle", description="The source handle ID")
    target_handle: Optional[str] = Field(None, alias="targetHandle", description="The target handle ID")
    type: Optional[str] = Field("smoothstep", description="The type of the edge")
    data: Optional[dict[str, JsonValue]] = Field(None, description="Additional data for the edge")


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
    nodes: list[WorkflowNodeOutput] = Field(..., description="List of workflow nodes")
    edges: list[WorkflowEdge] = Field(..., description="List of workflow edges")


# ---- Workflow models ----
class WorkflowCreate(BaseSchema):
    name: str = Field(..., description="Workflow name")
    description: Optional[str] = Field(None, description="Detailed description of the workflow")
    tags: list[str] = Field(default_factory=list, description="List of workflow tags")
    nodes: list[WorkflowNodeInput] = Field(..., description="List of workflow nodes")
    edges: list[WorkflowEdge] = Field(..., description="List of workflow edges")


class WorkflowUpdate(BaseSchema):
    name: Optional[str] = Field(None, description="Workflow name")
    description: Optional[str] = Field(None, description="Detailed description of the workflow")
    nodes: Optional[list[WorkflowNodeInput]] = Field(None, description="List of workflow nodes")
    edges: Optional[list[WorkflowEdge]] = Field(None, description="List of workflow edges")
    status: Optional[WorkflowStatus] = Field(None, description="Workflow status")
    tags: Optional[list[str]] = Field(None, description="List of workflow tags")


class WorkflowDuplicateRequest(BaseSchema):
    name: Optional[str] = Field(None, description="New name for the duplicated workflow")


class WorkflowValidateRequest(BaseSchema):
    nodes: list[WorkflowNodeInput] = Field(..., description="List of workflow nodes to validate")
    edges: list[WorkflowEdge] = Field(..., description="List of workflow edges to validate")


class WorkflowValidationResponse(BaseSchema):
    valid: bool = Field(..., description="Whether the workflow passed validation")
    errors: list[str] = Field(..., description="List of validation error messages")


class WorkflowVariable(BaseSchema):
    name: str = Field(..., description="Variable name")
    type: str = Field(..., description="Variable type (string, number, boolean, object)")
    description: Optional[str] = Field(None, description="Variable description")


class NodeVariables(BaseSchema):
    node_id: str = Field(..., description="Node ID")
    reference_key: str = Field(..., description="Node reference key")
    node_type: str = Field(..., description="Node type")
    node_label: str = Field(..., description="Node label")
    outputs: list[WorkflowVariable] = Field(..., description="Output variables of the node")


class WorkflowVariablesResponse(BaseSchema):
    variables: list[NodeVariables] = Field(..., description="List of available variables per node")


class WorkflowSummary(BaseSchema):
    name: str = Field(..., description="Workflow name")
    description: Optional[str] = Field(None, description="Detailed description of the workflow")
    tags: list[str] = Field(default_factory=list, description="List of workflow tags")
    id: str = Field(..., description="Unique identifier of the workflow")
    status: WorkflowStatus = Field(..., description="Current status")
    version: int = Field(..., description="Version number")
    updated_at: datetime = Field(..., description="Last update time")


class PaginatedWorkflowSummaryResponse(BaseSchema):
    data: list[WorkflowSummary] = Field(..., description="List of items for current page")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class WorkflowInDB(BaseSchema):
    name: str = Field(..., description="Workflow name")
    description: Optional[str] = Field(None, description="Detailed description of the workflow")
    tags: list[str] = Field(default_factory=list, description="List of workflow tags")
    id: str = Field(..., description="Unique identifier of the workflow")
    definition: WorkflowDefinition = Field(..., description="Workflow graph definition")
    status: WorkflowStatus = Field(..., description="Current status")
    version: int = Field(..., description="Version number")
    created_by: Optional[str] = Field(None, description="Creator ID")
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


# ---- Executions ----
class WorkflowExecuteRequest(BaseSchema):
    inputs: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Input variables passed to the start node, where Key is the variable name",
    )
    stream: bool = Field(False, description="Whether to stream the execution events using SSE")
    async_mode: bool = Field(False, alias="async", description="Whether to execute asynchronously via Celery")


class WorkflowSyncResponseMetadata(BaseSchema):
    duration: float
    start_time: datetime = Field(..., alias="startTime")
    end_time: datetime = Field(..., alias="endTime")


class WorkflowSyncResponse(BaseSchema):
    success: bool
    output: JsonValue
    metadata: WorkflowSyncResponseMetadata


class NodeExecution(BaseSchema):
    node_id: str = Field(..., description="The node ID in the workflow")
    node_type: str = Field(..., description="Node type")
    status: ExecutionStatus = Field(..., description="Execution status")
    input: Optional[dict[str, JsonValue]] = Field(None, description="Input data for the node")
    output: JsonValue = Field(None, description="Output results for the node")
    error: Optional[str] = Field(None, description="Execution error message")
    started_at: datetime = Field(..., description="Node execution start time")
    completed_at: Optional[datetime] = Field(None, description="Node execution completion time")
    duration: Optional[int] = Field(None, description="Execution duration in milliseconds")
    id: str = Field(..., description="Unique identifier for the node execution record")
    execution_id: str = Field(..., description="The ID of the parent workflow execution record")


class WorkflowExecution(BaseSchema):
    workflow_id: str = Field(..., description="Workflow ID")
    status: ExecutionStatus = Field(..., description="Overall execution status")
    input: Optional[dict[str, JsonValue]] = Field(None, description="Workflow startup input data")
    output: JsonValue = Field(None, description="Final workflow output result")
    error: Optional[str] = Field(None, description="Execution error message")
    started_at: datetime = Field(..., description="Workflow execution start time")
    completed_at: Optional[datetime] = Field(None, description="Workflow execution completion time")
    duration: Optional[int] = Field(None, description="Execution duration in milliseconds")
    id: str = Field(..., description="Unique identifier for the workflow execution record")
    node_executions: list[NodeExecution] = Field(
        default_factory=list, description="List of detailed node executions"
    )


class WorkflowExecutionCancelResponse(BaseSchema):
    id: str = Field(..., description="Execution ID")
    status: ExecutionStatus = Field(..., description="Current status")
    cancelled_at: datetime = Field(..., description="Cancellation time")
