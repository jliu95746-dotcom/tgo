"""MCP (Model Control Protocol) schemas for AI service integration."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema


# Enums
from enum import Enum


class ToolSourceType(str, Enum):
    """Tool source type enumeration."""

    MCP_SERVER = "MCP_SERVER"
    CUSTOM = "CUSTOM"


class ToolStatus(str, Enum):
    """Tool status enumeration."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DEPRECATED = "DEPRECATED"


# Request Schemas
class CreateCustomToolRequest(BaseSchema):
    """Schema for creating a custom tool."""

    name: str = Field(..., description="Tool name (must be unique)")
    title: str = Field(..., description="Tool display title")
    description: Optional[str] = Field(
        default=None, description="Tool description"
    )
    webhook_url: str = Field(
        ..., description="Webhook URL to call when tool is executed"
    )
    input_schema: Dict[str, Any] = Field(
        ..., description="JSON schema for tool input validation"
    )
    timeout_seconds: Optional[int] = Field(
        default=30, description="Tool execution timeout in seconds"
    )
    retry_config: Optional[Dict[str, Any]] = Field(
        default=None, description="Retry configuration"
    )


class UpdateCustomToolRequest(BaseSchema):
    """Schema for updating a custom tool."""

    title: Optional[str] = Field(
        default=None, description="Updated tool display title"
    )
    description: Optional[str] = Field(
        default=None, description="Updated tool description"
    )
    webhook_url: Optional[str] = Field(
        default=None, description="Updated webhook URL"
    )
    input_schema: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Updated JSON schema for tool input validation",
    )
    timeout_seconds: Optional[int] = Field(
        default=None, description="Updated tool execution timeout in seconds"
    )
    retry_config: Optional[Dict[str, Any]] = Field(
        default=None, description="Updated retry configuration"
    )


class ExecuteToolRequest(BaseSchema):
    """Schema for executing a tool."""

    input_data: Dict[str, Any] = Field(
        ..., description="Input parameters for the tool execution"
    )


class InstallToolRequest(BaseSchema):
    """Schema for installing a tool in a project."""

    tool_id: UUID = Field(..., description="Tool ID to install")
    configuration: Optional[Dict[str, Any]] = Field(
        default=None, description="Project-specific tool configuration"
    )
    is_enabled: bool = Field(
        default=True,
        description="Whether to enable the tool after installation",
    )


class BulkInstallRequest(BaseSchema):
    """Schema for bulk installing tools."""

    project_id: UUID = Field(..., description="Project ID to install tools in")
    tool_ids: List[UUID] = Field(
        ..., description="List of tool IDs to install"
    )
    default_configuration: Optional[Dict[str, Any]] = Field(
        default=None, description="Default configuration for all tools"
    )
    enable_all: bool = Field(
        default=True,
        description="Whether to enable all tools after installation",
    )


class UpdateProjectToolRequest(BaseSchema):
    """Schema for updating a project tool installation."""

    is_enabled: Optional[bool] = Field(
        default=None, description="Whether the tool is enabled"
    )
    configuration: Optional[Dict[str, Any]] = Field(
        default=None, description="Updated project-specific tool configuration"
    )


# Response Schemas
class ToolExecutionResult(BaseSchema):
    """Schema for successful tool execution results."""

    execution_id: UUID = Field(..., description="Unique execution identifier")
    status: str = Field(..., description="Execution status")
    output_data: Optional[Dict[str, Any]] = Field(
        ..., description="Tool execution output data"
    )
    execution_time_ms: Optional[int] = Field(
        ..., description="Execution time in milliseconds"
    )
    retry_count: int = Field(
        default=0, description="Number of retry attempts made"
    )
    executed_at: datetime = Field(..., description="Execution timestamp")
    correlation_id: Optional[str] = Field(
        ..., description="Correlation ID for request tracing"
    )


class Tool(BaseSchema):
    """Complete tool schema for responses."""

    id: UUID = Field(..., description="Tool ID")
    name: str = Field(..., description="Tool name")
    title: str = Field(..., description="Tool display title")
    description: Optional[str] = Field(
        default=None, description="Tool description"
    )
    source_type: ToolSourceType = Field(..., description="Tool source type")
    status: ToolStatus = Field(..., description="Tool status")
    is_enabled: bool = Field(..., description="Whether the tool is enabled")
    input_schema: Dict[str, Any] = Field(
        ..., description="JSON schema for tool input"
    )
    webhook_url: Optional[str] = Field(
        default=None, description="Webhook URL for custom tools"
    )
    timeout_seconds: Optional[int] = Field(
        default=None, description="Tool execution timeout"
    )
    retry_config: Optional[Dict[str, Any]] = Field(
        default=None, description="Retry configuration"
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class ToolSummary(BaseSchema):
    """Lightweight tool summary for list responses."""

    id: UUID = Field(..., description="Tool ID")
    name: str = Field(..., description="Tool name")
    title: str = Field(..., description="Tool display title")
    description: Optional[str] = Field(
        default=None, description="Tool description"
    )
    source_type: ToolSourceType = Field(..., description="Tool source type")
    status: ToolStatus = Field(..., description="Tool status")
    is_public: bool = Field(..., description="Whether tool is public")
    mcp_server_id: Optional[UUID] = Field(
        default=None,
        description="UUID of the MCP server that provides this tool",
    )
    input_schema: Dict[str, Any] = Field(
        ...,
        description=(
            'JSON schema defining the expected input parameters for the tool'
        ),
    )
    output_schema: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            'JSON schema defining the expected output format from the tool'
        ),
    )
    short_no: Optional[str] = Field(
        default=None,
        description="Short identifier/number from the associated MCP server",
    )
    is_installed: Optional[bool] = Field(
        default=None,
        description=(
            'Whether the tool is installed/enabled in the current project '
            '(null if no project context)'
        ),
    )
    is_enabled: Optional[bool] = Field(
        default=None,
        description=(
            'Whether the tool is enabled in the current project (null if not'
            ' installed)'
        ),
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class ProjectTool(BaseSchema):
    """Complete project tool schema for responses."""

    id: UUID = Field(..., description="Project tool installation ID")
    tool_id: UUID = Field(..., description="Tool ID")
    project_id: UUID = Field(..., description="Project ID")
    is_enabled: bool = Field(
        ..., description="Whether the tool is enabled in this project"
    )
    configuration: Dict[str, Any] = Field(
        ..., description="Project-specific tool configuration"
    )
    installed_at: datetime = Field(
        ..., description="Tool installation timestamp"
    )
    tool: Optional[Tool] = Field(default=None, description="Full tool details")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class ProjectToolSummary(BaseSchema):
    """Lightweight project tool summary for list responses."""

    id: UUID = Field(..., description="Project tool installation ID")
    tool_id: UUID = Field(..., description="Tool ID")
    project_id: UUID = Field(..., description="Project ID")
    is_enabled: bool = Field(
        ..., description="Whether the tool is enabled in this project"
    )
    configuration: Dict[str, Any] = Field(
        ..., description="Project-specific tool configuration"
    )
    installed_at: datetime = Field(
        ..., description="Tool installation timestamp"
    )
    tool_name: str = Field(..., description="Tool name")
    tool_title: str = Field(..., description="Tool display title")
    tool_status: ToolStatus = Field(..., description="Tool status")
    tool_description: Optional[str] = Field(
        default=None,
        description=(
            'Tool description providing context about what the tool does'
        ),
    )
    tool_category: Optional[str] = Field(
        default=None,
        description="Tool category for organization and filtering",
    )
    mcp_server_id: Optional[UUID] = Field(
        default=None,
        description="UUID of the MCP server that provides this tool",
    )
    input_schema: Dict[str, Any] = Field(
        ...,
        description=(
            'JSON schema defining the expected input parameters for the tool'
        ),
    )
    output_schema: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            'JSON schema defining the expected output format from the tool'
        ),
    )
    short_no: Optional[str] = Field(
        default=None,
        description="Short identifier/number from the associated MCP server",
    )
    mcp_server: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Complete MCP server information (null for custom tools)",
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class ProjectToolStats(BaseSchema):
    """Project tool statistics schema."""

    total_installed: int = Field(
        ..., description="Total number of installed tools"
    )
    enabled_tools: int = Field(..., description="Number of enabled tools")
    disabled_tools: int = Field(..., description="Number of disabled tools")
    mcp_server_tools: int = Field(
        ..., description="Number of MCP server tools"
    )
    custom_tools: int = Field(..., description="Number of custom tools")
    active_tools: int = Field(
        ..., description="Number of recently active tools"
    )
    recent_installations: int = Field(
        ..., description="Number of recent installations"
    )


# Pagination Schema (matches MCP service exactly)
class PaginationMeta(BaseSchema):
    """Pagination metadata - matches MCP service PaginationMeta exactly."""

    page: int = Field(..., ge=1, description="Current page number")
    limit: int = Field(..., ge=1, le=100, description="Items per page")
    total: int = Field(..., ge=0, description="Total number of items")
    has_next: bool = Field(..., description="Whether there are more pages")
    has_prev: bool = Field(..., description="Whether there are previous pages")


# List Response Schemas
class ToolListResponse(BaseSchema):
    """Paginated response for tool list endpoints."""

    timestamp: datetime = Field(..., description="Response timestamp")
    request_id: Optional[str] = Field(
        default=None, description="Unique request identifier for tracing"
    )
    data: List[ToolSummary] = Field(..., description="List of tools")
    meta: PaginationMeta = Field(..., description="Pagination metadata")


class ProjectToolListResponse(BaseSchema):
    """Paginated response for project tool list endpoints."""

    timestamp: datetime = Field(..., description="Response timestamp")
    request_id: Optional[str] = Field(
        default=None, description="Unique request identifier for tracing"
    )
    data: List[ProjectToolSummary] = Field(
        ..., description="List of project tools"
    )
    meta: PaginationMeta = Field(..., description="Pagination metadata")


# Single Item Response Schemas
class ToolResponse(BaseSchema):
    """Response for single tool endpoints."""

    timestamp: datetime = Field(..., description="Response timestamp")
    request_id: Optional[str] = Field(
        default=None, description="Unique request identifier for tracing"
    )
    data: Tool = Field(..., description="Tool data")


class ProjectToolResponse(BaseSchema):
    """Response for single project tool endpoints."""

    timestamp: datetime = Field(..., description="Response timestamp")
    request_id: Optional[str] = Field(
        default=None, description="Unique request identifier for tracing"
    )
    data: ProjectTool = Field(..., description="Project tool data")
