from pydantic import BaseModel, JsonValue, Field
from typing import List, Optional, Dict, Literal
from datetime import datetime
from enum import Enum

# --- Enums ---

class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class SSEEventType(str, Enum):
    WORKFLOW_STARTED = "workflow_started"
    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    WORKFLOW_FINISHED = "workflow_finished"

# --- SSE Event Models ---

class SSEEventBase(BaseModel):
    workflow_run_id: str = Field(..., description="Workflow execution ID")
    task_id: str = Field(..., description="Celery task ID or placeholder")

class WorkflowStartedData(BaseModel):
    id: str = Field(..., description="Execution ID")
    workflow_id: str = Field(..., description="Workflow ID")
    inputs: Dict[str, JsonValue] = Field(..., description="Workflow startup input data")
    created_at: int = Field(..., description="Creation timestamp")

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
    inputs: Optional[Dict[str, JsonValue]] = Field(None, description="Input data for the node")
    outputs: Optional[JsonValue] = Field(None, description="Output results for the node")
    status: str = Field(..., description="Execution status (succeeded | failed)")
    error: Optional[str] = Field(None, description="Execution error message")
    elapsed_time: float = Field(..., description="Execution duration in seconds")
    finished_at: int = Field(..., description="Completion timestamp")

class NodeFinishedEvent(SSEEventBase):
    event: Literal["node_finished"] = Field("node_finished")
    data: NodeFinishedData

class WorkflowFinishedData(BaseModel):
    id: str = Field(..., description="Workflow execution record ID")
    workflow_id: str = Field(..., description="Workflow ID")
    status: str = Field(..., description="Overall execution status (succeeded | failed | cancelled)")
    outputs: Optional[JsonValue] = Field(None, description="Final workflow output result")
    error: Optional[str] = Field(None, description="Execution error message")
    elapsed_time: float = Field(..., description="Overall execution duration in seconds")
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
    input: Optional[Dict[str, JsonValue]] = Field(None, description="Input data for the node")
    output: Optional[JsonValue] = Field(None, description="Output results for the node")
    error: Optional[str] = Field(None, description="Execution error message")
    started_at: datetime = Field(..., description="Node execution start time")
    completed_at: Optional[datetime] = Field(None, description="Node execution completion time")
    duration: Optional[int] = Field(None, description="Execution duration in milliseconds")

class NodeExecution(NodeExecutionBase):
    id: str = Field(..., description="Unique identifier for the node execution record")
    execution_id: str = Field(..., description="The ID of the parent workflow execution record")

    class Config:
        from_attributes = True

class WorkflowExecutionBase(BaseModel):
    project_id: str = Field(..., description="Project ID")
    workflow_id: str = Field(..., description="Workflow ID")
    status: ExecutionStatus = Field(..., description="Overall execution status")
    input: Optional[Dict[str, JsonValue]] = Field(None, description="Workflow startup input data")
    output: Optional[JsonValue] = Field(None, description="Final workflow output result")
    error: Optional[str] = Field(None, description="Execution error message")
    started_at: datetime = Field(..., description="Workflow execution start time")
    completed_at: Optional[datetime] = Field(None, description="Workflow execution completion time")
    duration: Optional[int] = Field(None, description="Execution duration in milliseconds")

class WorkflowExecution(WorkflowExecutionBase):
    id: str = Field(..., description="Unique identifier for the workflow execution record")
    node_executions: List[NodeExecution] = Field([], description="List of detailed node executions")

    class Config:
        from_attributes = True

# --- API Request/Response Models ---

class WorkflowExecuteRequest(BaseModel):
    inputs: Dict[str, JsonValue] = Field(default={}, description="Input variables passed to the start node")
    stream: bool = Field(default=False, description="Whether to stream the execution events using Server-Sent Events (SSE)")
    async_mode: bool = Field(default=False, alias="async", description="Whether to execute asynchronously via Celery")

    class Config:
        populate_by_name = True

class WorkflowSyncResponseMetadata(BaseModel):
    duration: float = Field(..., description="Execution duration in seconds")
    startTime: str = Field(..., description="ISO format start time")
    endTime: str = Field(..., description="ISO format end time")

class WorkflowSyncResponse(BaseModel):
    success: bool = Field(..., description="Whether the execution was successful")
    output: JsonValue = Field(..., description="Workflow output results")
    metadata: WorkflowSyncResponseMetadata

class WorkflowExecutionCancelResponse(BaseModel):
    id: str = Field(..., description="Execution ID")
    status: ExecutionStatus = Field(..., description="Current status")
    cancelled_at: datetime = Field(..., description="Cancellation time")
