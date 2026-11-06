from pydantic import BaseModel, ConfigDict, JsonValue, Field, field_serializer
from typing import List, Optional, Dict, Literal
from datetime import datetime
from enum import Enum
from uuid import UUID
from pydantic import model_validator
from app.schemas.capabilities import ExecutionMode

# --- Enums ---


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PENDING_RESUME = "pending_resume"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SSEEventType(str, Enum):
    WORKFLOW_STARTED = "workflow_started"
    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    WORKFLOW_FINISHED = "workflow_finished"


def public_input_evidence(
    inputs: Dict[str, JsonValue],
) -> Dict[str, JsonValue]:
    if inputs.get("capture_status") == "redacted":
        evidence: Dict[str, JsonValue] = {"capture_status": "redacted"}
        field_count = inputs.get("field_count")
        if type(field_count) is int and 0 <= field_count <= 10000:
            evidence["field_count"] = field_count
        return evidence
    return {"capture_status": "redacted", "field_count": len(inputs)}


def public_node_output_evidence(
    node_type: str, output: JsonValue | None
) -> JsonValue | None:
    """
    Keep final answers and verified trace coordinates, not legacy node
    payloads.
    """
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
        return public_input_evidence(output)
    if node_type == "condition" and type(output.get("result")) is bool:
        return {"result": output["result"]}
    if node_type == "approval" and output.get("requested") is True:
        return {"requested": True}
    evidence = public_input_evidence(output)
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


# --- SSE Event Models ---


class SSEEventBase(BaseModel):
    workflow_run_id: str = Field(..., description="Workflow execution ID")
    task_id: str = Field(..., description="Celery task ID or placeholder")


class WorkflowStartedData(BaseModel):
    id: str = Field(..., description="Execution ID")
    workflow_id: str = Field(..., description="Workflow ID")
    inputs: Dict[str, JsonValue] = Field(
        ..., description="Workflow startup input data"
    )
    created_at: int = Field(..., description="Creation timestamp")

    @field_serializer("inputs")
    def serialize_inputs(
        self, inputs: Dict[str, JsonValue]
    ) -> Dict[str, JsonValue]:
        return public_input_evidence(inputs)


class WorkflowStartedEvent(SSEEventBase):
    event: Literal["workflow_started"] = Field("workflow_started")
    data: WorkflowStartedData


class NodeStartedData(BaseModel):
    id: str = Field(..., description="Node execution record ID")
    node_id: str = Field(..., description="Node ID in workflow")
    node_type: str = Field(..., description="Node type")
    title: str = Field(..., description="Node title")
    index: int = Field(..., description="Execution step index")
    created_at: int = Field(..., description="Creation timestamp")


class NodeStartedEvent(SSEEventBase):
    event: Literal["node_started"] = Field("node_started")
    data: NodeStartedData


class NodeFinishedData(BaseModel):
    id: str = Field(..., description="Node execution record ID")
    node_id: str = Field(..., description="Node ID in workflow")
    node_type: str = Field(..., description="Node type")
    inputs: Optional[Dict[str, JsonValue]] = Field(
        None, description="Input data for the node"
    )
    outputs: Optional[JsonValue] = Field(
        None, description="Output results for the node"
    )
    status: str = Field(
        ..., description="Execution status (succeeded | failed)"
    )
    error: Optional[str] = Field(None, description="Execution error message")
    elapsed_time: float = Field(
        ..., description="Execution duration in seconds"
    )
    finished_at: int = Field(..., description="Completion timestamp")

    @field_serializer("inputs")
    def serialize_inputs(
        self,
        inputs: Dict[str, JsonValue] | None,
    ) -> Dict[str, JsonValue] | None:
        return public_input_evidence(inputs) if inputs is not None else None

    @field_serializer("outputs")
    def serialize_outputs(self, outputs: JsonValue | None) -> JsonValue | None:
        return public_node_output_evidence(self.node_type, outputs)


class NodeFinishedEvent(SSEEventBase):
    event: Literal["node_finished"] = Field("node_finished")
    data: NodeFinishedData


class WorkflowFinishedData(BaseModel):
    id: str = Field(..., description="Workflow execution record ID")
    workflow_id: str = Field(..., description="Workflow ID")
    status: str = Field(
        ...,
        description=(
            "Overall execution status (succeeded | failed | cancelled)"
        ),
    )
    outputs: Optional[JsonValue] = Field(
        None, description="Final workflow output result"
    )
    error: Optional[str] = Field(None, description="Execution error message")
    elapsed_time: float = Field(
        ..., description="Overall execution duration in seconds"
    )
    total_steps: int = Field(..., description="Total nodes executed")
    finished_at: int = Field(..., description="Completion timestamp")


class WorkflowFinishedEvent(SSEEventBase):
    event: Literal["workflow_finished"] = Field("workflow_finished")
    data: WorkflowFinishedData


# --- Core Database Models ---


class NodeExecutionBase(BaseModel):
    project_id: str = Field(..., description="Project ID")
    node_id: str = Field(..., description="The node ID in the workflow")
    node_type: str = Field(..., description="Node type")
    status: ExecutionStatus = Field(..., description="Execution status")
    input: Optional[Dict[str, JsonValue]] = Field(
        None, description="Input data for the node"
    )
    output: Optional[JsonValue] = Field(
        None, description="Output results for the node"
    )
    error: Optional[str] = Field(None, description="Execution error message")
    started_at: datetime = Field(..., description="Node execution start time")
    completed_at: Optional[datetime] = Field(
        None, description="Node execution completion time"
    )
    duration: Optional[int] = Field(
        None, description="Execution duration in milliseconds"
    )

    @field_serializer("input")
    def serialize_input(
        self,
        inputs: Dict[str, JsonValue] | None,
    ) -> Dict[str, JsonValue] | None:
        return public_input_evidence(inputs) if inputs is not None else None

    @field_serializer("output")
    def serialize_output(self, output: JsonValue | None) -> JsonValue | None:
        return public_node_output_evidence(self.node_type, output)


class NodeExecution(NodeExecutionBase):
    id: str = Field(
        ..., description="Unique identifier for the node execution record"
    )
    execution_id: str = Field(
        ..., description="The ID of the parent workflow execution record"
    )

    class Config:
        from_attributes = True


class WorkflowExecutionBase(BaseModel):
    execution_mode: ExecutionMode | None = None
    workflow_version: int | None = None
    project_id: str = Field(..., description="Project ID")
    workflow_id: str = Field(..., description="Workflow ID")
    status: ExecutionStatus = Field(
        ..., description="Overall execution status"
    )
    input: Optional[Dict[str, JsonValue]] = Field(
        None, description="Workflow startup input data"
    )
    output: Optional[JsonValue] = Field(
        None, description="Final workflow output result"
    )
    error: Optional[str] = Field(None, description="Execution error message")
    started_at: datetime = Field(
        ..., description="Workflow execution start time"
    )
    completed_at: Optional[datetime] = Field(
        None, description="Workflow execution completion time"
    )
    duration: Optional[int] = Field(
        None, description="Execution duration in milliseconds"
    )

    @field_serializer("input")
    def serialize_input(
        self,
        inputs: Dict[str, JsonValue] | None,
    ) -> Dict[str, JsonValue] | None:
        return public_input_evidence(inputs) if inputs is not None else None


class WorkflowExecution(WorkflowExecutionBase):
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
    id: str = Field(
        ..., description="Unique identifier for the workflow execution record"
    )
    node_executions: List[NodeExecution] = Field(
        [], description="List of detailed node executions"
    )

    class Config:
        from_attributes = True


# --- API Request/Response Models ---


class WorkflowExecutionOrigin(BaseModel):
    """Trace coordinates supplied by the internal employee runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ai_request_id: str = Field(min_length=1, max_length=128)
    business_project_id: UUID | None = None
    business_request_id: UUID | None = None
    visitor_session_id: UUID | None = None
    source_message_id: str | None = Field(
        default=None, min_length=1, max_length=255
    )
    source_channel: (
        Literal["web", "app", "wecom_kf", "phone", "internal"] | None
    ) = None
    parent_execution_id: UUID | None = None
    workflow_lineage: tuple[str, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def complete_business_origin(self) -> "WorkflowExecutionOrigin":
        business_fields = (
            self.business_project_id,
            self.business_request_id,
            self.visitor_session_id,
        )
        if any(value is not None for value in business_fields) and not all(
            value is not None for value in business_fields
        ):
            raise ValueError("客户业务请求来源必须包含企业、请求和会话身份")
        if (self.source_message_id is None) != (self.source_channel is None):
            raise ValueError("客户原消息编号和渠道必须同时提供")
        if (
            self.source_message_id is not None
            and self.business_request_id is None
        ):
            raise ValueError("客户原消息必须关联已核验的业务请求")
        if (self.parent_execution_id is None) != (not self.workflow_lineage):
            raise ValueError("父工作流编号和调用链必须同时提供")
        if len(set(self.workflow_lineage)) != len(
            self.workflow_lineage
        ) or any(not 1 <= len(item) <= 128 for item in self.workflow_lineage):
            raise ValueError("父工作流调用链无效")
        return self


class WorkflowExecuteRequest(BaseModel):
    employee_agent_id: UUID | None = None
    invocation_key: UUID | None = None
    release_version: int | None = Field(default=None, ge=1)
    expected_release_id: str | None = Field(default=None, min_length=1)
    expected_release_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    execution_mode: ExecutionMode = "draft_test"
    origin: WorkflowExecutionOrigin | None = None
    inputs: Dict[str, JsonValue] = Field(
        default={}, description="Input variables passed to the start node"
    )
    stream: bool = Field(
        default=False,
        description=(
            "Whether to stream the execution events using Server-Sent Events"
            " (SSE)"
        ),
    )
    async_mode: bool = Field(
        default=False,
        alias="async",
        description="Whether to execute asynchronously via Celery",
    )

    class Config:
        populate_by_name = True

    @model_validator(mode="after")
    def employee_run_requires_fixed_production_release(
        self,
    ) -> "WorkflowExecuteRequest":
        if self.origin is not None and self.employee_agent_id is None:
            raise ValueError("运行来源仅允许已核验的员工内部调用")
        if self.employee_agent_id is not None and (
            self.execution_mode != "production"
            or self.release_version is None
            or self.expected_release_id is None
            or self.expected_release_digest is None
        ):
            raise ValueError("员工工作流执行必须指定完整的固定发布版本")
        return self


class WorkflowSyncResponseMetadata(BaseModel):
    duration: float = Field(..., description="Execution duration in seconds")
    startTime: str = Field(..., description="ISO format start time")
    endTime: str = Field(..., description="ISO format end time")


class WorkflowSyncResponse(BaseModel):
    success: bool = Field(
        ..., description="Whether the execution was successful"
    )
    output: JsonValue = Field(..., description="Workflow output results")
    metadata: WorkflowSyncResponseMetadata


class WorkflowExecutionCancelResponse(BaseModel):
    id: str = Field(..., description="Execution ID")
    status: ExecutionStatus = Field(..., description="Current status")
    cancelled_at: datetime = Field(..., description="Cancellation time")
