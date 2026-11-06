from pydantic import BaseModel, Field, JsonValue
from typing import List, Literal, Optional, Dict
from datetime import datetime
from enum import Enum
from app.schemas.node import WorkflowNode


class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class WorkflowEdge(BaseModel):
    id: str = Field(..., description="The unique identifier of the edge")
    source: str = Field(..., description="The source node ID")
    target: str = Field(..., description="The target node ID")
    sourceHandle: Optional[str] = Field(
        None, description="The source handle ID"
    )
    targetHandle: Optional[str] = Field(
        None, description="The target handle ID"
    )
    type: Optional[str] = Field(
        "smoothstep", description="The type of the edge"
    )
    data: Optional[Dict[str, JsonValue]] = Field(
        None, description="Additional data for the edge"
    )


class WorkflowDefinition(BaseModel):
    nodes: List[WorkflowNode] = Field(
        ..., description="List of workflow nodes"
    )
    edges: List[WorkflowEdge] = Field(
        ..., description="List of workflow edges"
    )


class WorkflowBase(BaseModel):
    name: str = Field(..., description="Workflow name")
    description: Optional[str] = Field(
        None, description="Detailed description of the workflow"
    )
    tags: List[str] = Field([], description="List of workflow tags")


class WorkflowCreate(WorkflowBase):
    nodes: List[WorkflowNode] = Field(
        ..., description="List of workflow nodes"
    )
    edges: List[WorkflowEdge] = Field(
        ..., description="List of workflow edges"
    )


class WorkflowUpdate(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    name: Optional[str] = Field(None, description="Workflow name")
    description: Optional[str] = Field(
        None, description="Detailed description of the workflow"
    )
    nodes: Optional[List[WorkflowNode]] = Field(
        None, description="List of workflow nodes"
    )
    edges: Optional[List[WorkflowEdge]] = Field(
        None, description="List of workflow edges"
    )
    status: Optional[WorkflowStatus] = Field(
        None, description="Workflow status"
    )
    tags: Optional[List[str]] = Field(
        None, description="List of workflow tags"
    )


class WorkflowInDB(WorkflowBase):
    published_version: int | None = None
    publication_revision: int = 0
    id: str = Field(..., description="Unique identifier of the workflow")
    project_id: str = Field(
        ..., description="The ID of the project this workflow belongs to"
    )
    definition: WorkflowDefinition = Field(
        ..., description="Workflow graph definition"
    )
    status: WorkflowStatus = Field(..., description="Current status")
    version: int = Field(..., description="Version number")
    created_by: Optional[str] = Field(None, description="Creator ID")
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")

    class Config:
        from_attributes = True


class WorkflowSummary(WorkflowBase):
    published_version: int | None = None
    publication_revision: int = 0
    id: str = Field(..., description="Unique identifier of the workflow")
    project_id: str = Field(
        ..., description="The ID of the project this workflow belongs to"
    )
    status: WorkflowStatus = Field(..., description="Current status")
    version: int = Field(..., description="Version number")
    updated_at: datetime = Field(..., description="Last update time")

    class Config:
        from_attributes = True


class WorkflowValidationResponse(BaseModel):
    valid: bool = Field(
        ..., description="Whether the workflow passed validation"
    )
    errors: List[str] = Field(
        ...,
        description=(
            "List of validation error messages, empty if valid is true"
        ),
    )


class WorkflowValidateRequest(BaseModel):
    nodes: List[WorkflowNode] = Field(
        ..., description="List of workflow nodes to validate"
    )
    edges: List[WorkflowEdge] = Field(
        ..., description="List of workflow edges to validate"
    )


class WorkflowDuplicateRequest(BaseModel):
    name: Optional[str] = Field(
        None, description="New name for the duplicated workflow"
    )


class WorkflowVariable(BaseModel):
    name: str = Field(..., description="Variable name")
    type: str = Field(
        ..., description="Variable type (string, number, boolean, object)"
    )
    description: Optional[str] = Field(
        None, description="Variable description"
    )


class NodeVariables(BaseModel):
    node_id: str = Field(..., description="Node ID")
    reference_key: str = Field(..., description="Node reference key")
    node_type: str = Field(..., description="Node type")
    node_label: str = Field(..., description="Node label")
    outputs: List[WorkflowVariable] = Field(
        ..., description="Output variables of the node"
    )


class WorkflowVariablesResponse(BaseModel):
    variables: List[NodeVariables] = Field(
        ..., description="List of available variables per node"
    )


class WorkflowInputParameter(BaseModel):
    name: str = Field(..., description="Parameter name")
    type: str = Field(
        ...,
        description="Parameter type (string, number, boolean, object, array)",
    )
    description: Optional[str] = Field(
        None, description="Parameter description"
    )
    required: bool = Field(
        False, description="Whether the parameter is required"
    )
    default: Optional[JsonValue] = Field(
        None, description="Default value for the parameter"
    )


class WorkflowBatchInfo(WorkflowSummary):
    """Workflow summary with input parameters for agent integration"""

    input_parameters: List[WorkflowInputParameter] = Field(
        default=[],
        description="List of input parameters required by the workflow",
    )


class WorkflowBusinessQueryDependency(BaseModel):
    connection_id: str
    operation: Literal["order_query", "logistics_query"]


class WorkflowIntegrationRelease(BaseModel):
    """
    A published version's stable input and dependency contract for employee
    bindings.
    """

    workflow_id: str
    release_version: int = Field(ge=1)
    release_id: str
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_parameters: List[WorkflowInputParameter]
    node_types: List[str]
    required_business_queries: List[WorkflowBusinessQueryDependency]
    approval_actions: List[str]
