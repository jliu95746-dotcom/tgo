"\nMCP Tools Schemas\n\nThis module defines Pydantic schemas for MCP \
service tools endpoints.\nThese schemas are used for request/response \
validation and OpenAPI documentation.\n"

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ToolSourceType(str, Enum):
    """Tool source type enumeration."""

    MCP_SERVER = "MCP_SERVER"
    CUSTOM = "CUSTOM"


class ToolStatus(str, Enum):
    """Tool status enumeration."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DEPRECATED = "DEPRECATED"
    MAINTENANCE = "MAINTENANCE"


class PaginationMeta(BaseModel):
    """Pagination metadata - matches MCP service PaginationMeta exactly."""

    page: int = Field(ge=1, description="Current page number")
    limit: int = Field(ge=1, le=100, description="Items per page")
    total: int = Field(ge=0, description="Total number of items")
    total_pages: int = Field(ge=0, description="Total number of pages")
    has_next: bool = Field(description="Whether there is a next page")
    has_prev: bool = Field(description="Whether there is a previous page")


class MCPToolRaw(BaseModel):
    (
        'Raw tool data as returned by MCP service - matches ToolSummary '
        'schema exactly.'
    )

    # Required fields from MCP service ToolSummary schema

    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")
    id: UUID = Field(description="Unique identifier")
    name: str = Field(max_length=255, min_length=1, description="Name")
    title: Optional[str] = Field(
        default=None, description="Human-readable display name for the tool"
    )
    description: Optional[str] = Field(
        default=None, max_length=1000, description="Description"
    )
    version: Optional[str] = Field(default=None, description="Tool version")
    category: Optional[str] = Field(default=None, description="Tool category")
    tags: List[str] = Field(default_factory=list, description="Tool tags")
    status: Optional[str] = Field(
        default=None, description="Tool status"
    )  # References ToolStatus enum
    tool_source_type: Optional[str] = Field(
        default=None, description="Tool source type"
    )  # References ToolSourceType enum
    is_public: bool = Field(
        default=False, description="Whether tool is public"
    )
    mcp_server_id: Optional[UUID] = Field(
        default=None,
        description="UUID of the MCP server that provides this tool",
    )
    input_schema: Dict[str, Any] = Field(
        default_factory=dict,
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

    # Additional fields for complete Tool schema (all optional since they're
    # only in full Tool, not ToolSummary)

    meta_data: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional metadata"
    )
    project_id: Optional[UUID] = Field(
        default=None, description="Project ID (for custom tools)"
    )
    execution_type: Optional[str] = Field(
        default=None, description="Execution method for custom tools"
    )
    webhook_url: Optional[str] = Field(
        default=None, description="Webhook URL for custom tools"
    )
    webhook_method: Optional[str] = Field(
        default=None, description="HTTP method for webhook calls"
    )
    timeout_ms: Optional[int] = Field(
        default=None, description="Execution timeout in milliseconds"
    )
    max_retries: Optional[int] = Field(
        default=None, description="Maximum retry attempts"
    )
    rate_limit_per_minute: Optional[int] = Field(
        default=None, description="Rate limit per minute"
    )
    mcp_server: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Complete MCP server information (null for custom tools)",
    )
    # Augmented field for agent context: whether this tool is enabled for the
    # agent binding

    enabled: Optional[bool] = Field(
        default=None,
        description=(
            'Whether the tool is enabled for the current agent binding'
        ),
    )

    class Config:
        extra = "allow"  # Allow additional fields from MCP service


class ToolSummary(BaseModel):
    (
        'Lightweight tool summary for list responses - includes all fields '
        'from MCP service.'
    )

    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")
    id: UUID = Field(description="Unique identifier")
    name: str = Field(description="Tool name")
    title: Optional[str] = Field(
        default=None, description="Human-readable display name for the tool"
    )
    description: Optional[str] = Field(
        default=None, description="Tool description"
    )
    version: str = Field(description="Tool version")
    category: Optional[str] = Field(default=None, description="Tool category")
    tags: List[str] = Field(default_factory=list, description="Tool tags")
    status: ToolStatus = Field(description="Tool status")
    source_type: ToolSourceType = Field(description="Tool source type")
    is_public: bool = Field(description="Whether tool is public")
    mcp_server_id: Optional[UUID] = Field(
        default=None,
        description="UUID of the MCP server that provides this tool",
    )
    input_schema: Dict[str, Any] = Field(
        default_factory=dict,
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
    is_enabled: bool = Field(description="Whether the tool is enabled")


class Tool(BaseModel):
    """Complete tool schema for responses."""

    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")
    id: UUID = Field(description="Tool ID")
    name: str = Field(description="Tool name")
    title: str = Field(description="Tool display title")
    description: Optional[str] = Field(
        default=None, description="Tool description"
    )
    source_type: ToolSourceType = Field(description="Tool source type")
    status: ToolStatus = Field(description="Tool status")
    is_enabled: bool = Field(description="Whether the tool is enabled")
    input_schema: Dict[str, Any] = Field(
        description="JSON schema for tool input"
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


class MCPToolListResponse(BaseModel):
    """Raw response from MCP service for tool list endpoints."""

    timestamp: Optional[datetime] = Field(
        default=None, description="Response timestamp"
    )
    request_id: Optional[str] = Field(
        default=None, description="Unique request identifier for tracing"
    )
    data: List[MCPToolRaw] = Field(
        description="List of raw tools from MCP service"
    )
    meta: PaginationMeta = Field(description="Pagination metadata")

    class Config:
        extra = "allow"  # Allow additional fields from MCP service


class MCPToolResponse(BaseModel):
    """Raw response from MCP service for single tool endpoints."""

    # Note: MCP service returns Tool directly, not wrapped in a response object
    # This schema represents the direct Tool response

    pass  # We'll use MCPToolRaw directly for single tool responses


class ToolListResponse(BaseModel):
    """Paginated response for tool list endpoints."""

    timestamp: datetime = Field(description="Response timestamp")
    request_id: Optional[str] = Field(
        default=None, description="Unique request identifier for tracing"
    )
    data: List[ToolSummary] = Field(
        description="List of items for the current page"
    )
    meta: PaginationMeta = Field(description="Pagination metadata")


class ToolResponse(BaseModel):
    """Response for single tool endpoints."""

    timestamp: datetime = Field(description="Response timestamp")
    request_id: Optional[str] = Field(
        default=None, description="Unique request identifier for tracing"
    )
    data: Tool = Field(description="Tool data")


class CreateCustomToolRequest(BaseModel):
    """Schema for creating a custom tool."""

    name: str = Field(description="Tool name (must be unique)")
    title: str = Field(description="Tool display title")
    description: Optional[str] = Field(
        default=None, description="Tool description"
    )
    webhook_url: str = Field(
        description="Webhook URL to call when tool is executed"
    )
    input_schema: Dict[str, Any] = Field(
        description="JSON schema for tool input validation"
    )
    timeout_seconds: Optional[int] = Field(
        default=30, description="Tool execution timeout in seconds"
    )
    retry_config: Optional[Dict[str, Any]] = Field(
        default=None, description="Retry configuration"
    )


class UpdateCustomToolRequest(BaseModel):
    """Schema for updating a custom tool."""

    title: Optional[str] = Field(
        default=None, description="Tool display title"
    )
    description: Optional[str] = Field(
        default=None, description="Tool description"
    )
    webhook_url: Optional[str] = Field(
        default=None, description="Webhook URL to call when tool is executed"
    )
    input_schema: Optional[Dict[str, Any]] = Field(
        default=None, description="JSON schema for tool input validation"
    )
    timeout_seconds: Optional[int] = Field(
        default=None, description="Tool execution timeout in seconds"
    )
    retry_config: Optional[Dict[str, Any]] = Field(
        default=None, description="Retry configuration"
    )
    is_enabled: Optional[bool] = Field(
        default=None, description="Whether the tool is enabled"
    )


class ExecuteToolRequest(BaseModel):
    """Schema for executing a tool."""

    input_data: Dict[str, Any] = Field(
        description="Input parameters for the tool execution"
    )


class ToolExecutionResult(BaseModel):
    (
        'Schema for successful tool execution results - matches MCP service '
        'ToolExecutionResult exactly.'
    )

    execution_id: UUID = Field(description="Unique execution identifier")
    status: str = Field(
        description="Execution status"
    )  # References ExecutionStatus enum
    output_data: Optional[Dict[str, Any]] = Field(
        description="Tool execution output data"
    )
    execution_time_ms: Optional[int] = Field(
        description="Execution time in milliseconds"
    )
    retry_count: int = Field(
        default=0, description="Number of retry attempts made"
    )
    executed_at: datetime = Field(description="Execution timestamp")
    correlation_id: Optional[str] = Field(
        description="Correlation ID for request tracing"
    )


class ToolExecutionError(BaseModel):
    """Schema for tool execution errors."""

    execution_id: UUID = Field(description="Unique execution ID")
    status: str = Field(description="Execution status")
    error_message: str = Field(description="Error message")
    error_code: Optional[str] = Field(default=None, description="Error code")
    execution_time_ms: Optional[int] = Field(
        description="Execution time in milliseconds"
    )
    retry_count: int = Field(
        default=0, description="Number of retry attempts made"
    )
    http_status_code: Optional[int] = Field(
        default=None, description="HTTP status code for webhook tools"
    )
    executed_at: datetime = Field(description="Execution timestamp")
    correlation_id: Optional[str] = Field(
        default=None, description="Correlation ID for request tracing"
    )


class ErrorResponse(BaseModel):
    """Standard error response schema."""

    timestamp: datetime = Field(description="Error timestamp")
    request_id: Optional[str] = Field(
        default=None, description="Request ID for tracing"
    )
    error_code: str = Field(description="Error code")
    message: str = Field(description="Error message")
    details: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional error details"
    )


# Project Tools Schemas


class MCPProjectToolRaw(BaseModel):
    (
        'Raw project tool data as returned by MCP service - matches '
        'ProjectToolSummary schema exactly.'
    )

    # Required fields from MCP service ProjectToolSummary schema

    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")
    id: UUID = Field(description="Unique identifier")
    project_id: UUID = Field(description="Project ID")
    tool_id: UUID = Field(description="Tool ID")
    is_enabled: bool = Field(description="Whether the tool is enabled")
    installed_at: datetime = Field(description="Tool installation timestamp")
    tool_name: Optional[str] = Field(default=None, description="Tool name")
    tool_title: Optional[str] = Field(
        default=None,
        description=(
            'Tool title (human-readable display name, falls back to name if '
            'not set)'
        ),
    )
    tool_version: Optional[str] = Field(
        default=None, description="Tool version"
    )
    tool_source_type: Optional[str] = Field(
        default=None, description="Tool source type"
    )
    tool_status: Optional[str] = Field(default=None, description="Tool status")
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
        default_factory=dict,
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

    # Additional fields for complete ProjectTool schema (only in full
    # ProjectTool, not ProjectToolSummary)

    configuration: Optional[Dict[str, Any]] = Field(
        default=None, description="Project-specific tool configuration"
    )
    tool: Optional[Dict[str, Any]] = Field(
        default=None, description="Tool information"
    )

    class Config:
        extra = "allow"  # Allow additional fields from MCP service


class MCPProjectToolListResponse(BaseModel):
    """Raw response from MCP service for project tool list endpoints."""

    timestamp: Optional[datetime] = Field(
        default=None, description="Response timestamp"
    )
    request_id: Optional[str] = Field(
        default=None, description="Unique request identifier for tracing"
    )
    data: List[MCPProjectToolRaw] = Field(
        description="List of raw project tools from MCP service"
    )
    meta: PaginationMeta = Field(description="Pagination metadata")

    class Config:
        extra = "allow"  # Allow additional fields from MCP service


class ProjectToolSummary(BaseModel):
    (
        'Lightweight project tool summary for list responses - includes all '
        'fields from MCP service.'
    )

    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")
    id: UUID = Field(description="Project tool installation ID")
    tool_id: UUID = Field(description="Tool ID")
    project_id: UUID = Field(description="Project ID")
    is_enabled: bool = Field(
        description="Whether the tool is enabled in this project"
    )
    configuration: Dict[str, Any] = Field(
        description="Project-specific tool configuration"
    )
    installed_at: datetime = Field(description="Tool installation timestamp")
    tool_name: str = Field(description="Tool name")
    tool_title: str = Field(description="Tool display title")
    tool_version: str = Field(description="Tool version")
    tool_source_type: ToolSourceType = Field(description="Tool source type")
    tool_status: ToolStatus = Field(description="Tool status")
    tool_description: Optional[str] = Field(
        description=(
            'Tool description providing context about what the tool does'
        )
    )
    tool_category: Optional[str] = Field(
        description="Tool category for organization and filtering"
    )
    mcp_server_id: Optional[UUID] = Field(
        description="UUID of the MCP server that provides this tool"
    )
    input_schema: Dict[str, Any] = Field(
        description=(
            'JSON schema defining the expected input parameters for the tool'
        )
    )
    output_schema: Optional[Dict[str, Any]] = Field(
        description=(
            'JSON schema defining the expected output format from the tool'
        )
    )
    short_no: Optional[str] = Field(
        description="Short identifier/number from the associated MCP server"
    )
    mcp_server: Optional[Dict[str, Any]] = Field(
        description="Complete MCP server information (null for custom tools)"
    )


class MCPProjectToolResponse(BaseModel):
    """Raw response from MCP service for single project tool endpoints."""

    timestamp: Optional[datetime] = Field(
        default=None, description="Response timestamp"
    )
    request_id: Optional[str] = Field(
        default=None, description="Unique request identifier for tracing"
    )
    data: MCPProjectToolRaw = Field(
        description="Raw project tool from MCP service"
    )

    class Config:
        extra = "allow"  # Allow additional fields from MCP service


class ProjectTool(BaseModel):
    """Complete project tool schema for responses."""

    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")
    id: UUID = Field(description="Project tool installation ID")
    tool_id: UUID = Field(description="Tool ID")
    project_id: UUID = Field(description="Project ID")
    is_enabled: bool = Field(
        description="Whether the tool is enabled in this project"
    )
    configuration: Dict[str, Any] = Field(
        description="Project-specific tool configuration"
    )
    installed_at: datetime = Field(description="Tool installation timestamp")
    tool: Optional[Tool] = Field(default=None, description="Full tool details")


class ProjectToolListResponse(BaseModel):
    """Paginated response for project tool list endpoints."""

    timestamp: datetime = Field(description="Response timestamp")
    request_id: Optional[str] = Field(
        default=None, description="Unique request identifier for tracing"
    )
    data: List[ProjectToolSummary] = Field(
        description="List of items for the current page"
    )
    meta: PaginationMeta = Field(description="Pagination metadata")


class ProjectToolResponse(BaseModel):
    """Response for single project tool endpoints."""

    timestamp: datetime = Field(description="Response timestamp")
    request_id: Optional[str] = Field(
        default=None, description="Unique request identifier for tracing"
    )
    data: ProjectTool = Field(description="Project tool data")


class InstallToolRequest(BaseModel):
    """Schema for installing a tool in a project."""

    tool_id: UUID = Field(description="Tool ID to install")
    configuration: Optional[Dict[str, Any]] = Field(
        default=None, description="Project-specific tool configuration"
    )
    is_enabled: bool = Field(
        default=True,
        description="Whether to enable the tool after installation",
    )


class UpdateProjectToolRequest(BaseModel):
    """Schema for updating a project tool installation."""

    is_enabled: Optional[bool] = Field(
        default=None, description="Whether the tool is enabled"
    )
    configuration: Optional[Dict[str, Any]] = Field(
        default=None, description="Project-specific tool configuration"
    )


class BulkInstallRequest(BaseModel):
    """Schema for bulk installing tools."""

    project_id: UUID = Field(description="Project ID to install tools in")
    tool_ids: List[UUID] = Field(description="List of tool IDs to install")
    default_configuration: Optional[Dict[str, Any]] = Field(
        default=None, description="Default configuration for all tools"
    )
    enable_all: bool = Field(
        default=True,
        description="Whether to enable all tools after installation",
    )


class ProjectToolStats(BaseModel):
    """Project tool statistics schema."""

    total_installed: int = Field(description="Total number of installed tools")
    enabled_tools: int = Field(description="Number of enabled tools")
    disabled_tools: int = Field(description="Number of disabled tools")
    mcp_server_tools: int = Field(description="Number of MCP server tools")
    custom_tools: int = Field(description="Number of custom tools")
    active_tools: int = Field(description="Number of recently active tools")
    recent_installations: int = Field(
        description="Number of recent installations"
    )
