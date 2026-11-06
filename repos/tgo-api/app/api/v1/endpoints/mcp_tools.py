"""MCP Tools endpoints - proxy to AI service MCP integration."""

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.common_responses import CREATE_RESPONSES, CRUD_RESPONSES, DELETE_RESPONSES, UPDATE_RESPONSES
from app.core.security import get_authenticated_project
from app.core.security import AuthenticatedProject
from app.schemas.mcp import (
    CreateCustomToolRequest,
    ExecuteToolRequest,
    ToolExecutionResult,
    ToolListResponse,
    ToolResponse,
    Tool,
    UpdateCustomToolRequest,
)
from app.services.ai_client import ai_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/batch",
    response_model=List[Tool],
    responses=CRUD_RESPONSES,
    summary="Get Tools By IDs",
    description="""
    Retrieve multiple tools by their IDs in a single request (order preserved).
    This proxies the AI service MCP endpoint for efficient batch fetching.
    """,
)
async def get_tools_by_ids(
    ids: List[UUID] = Query(..., description="List of tool IDs to fetch"),
) -> List[Tool]:
    """Batch get tools from AI service MCP integration."""
    logger.info(
        "Getting MCP tools by IDs",
        extra={"count": len(ids), "ids": [str(i) for i in ids]},
    )

    result = await ai_client.get_mcp_tools_by_ids(
        ids=[str(i) for i in ids],
    )

    return [Tool.model_validate(item) for item in result]


@router.get(
    "",
    response_model=ToolListResponse,
    responses=CRUD_RESPONSES,
    summary="List Tools",
    description="""
    Retrieve a paginated list of available tools from the MCP service via AI service.

    This endpoint provides access to both MCP server tools and custom webhook tools
    with filtering capabilities by source type, status, and search terms.
    """,
)
async def list_tools(
    source_type: Optional[str] = Query(
        None, description="Filter by tool source type (MCP_SERVER, CUSTOM)"
    ),
    status: Optional[str] = Query(
        None, description="Filter by tool status (ACTIVE, INACTIVE, DEPRECATED)"
    ),
    search: Optional[str] = Query(
        None, description="Search term for tool name or description"
    ),
    page: int = Query(
        1, ge=1, description="Page number"
    ),
    per_page: int = Query(
        20, ge=1, le=100, description="Items per page"
    ),
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> ToolListResponse:
    """List tools from AI service MCP integration."""
    logger.info(
        "Listing MCP tools",
        extra={
            "source_type": source_type,
            "status": status,
            "search": search,
            "page": page,
            "per_page": per_page,
        }
    )
    project, _ = project_and_api_key

    result = await ai_client.list_mcp_tools(
        project_id=str(project.id),
        source_type=source_type,
        status=status,
        search=search,
        page=page,
        per_page=per_page,
    )

    return ToolListResponse.model_validate(result)


@router.get(
    "/{tool_id}",
    response_model=ToolResponse,
    responses=CRUD_RESPONSES,
    summary="Get Tool",
    description="""
    Retrieve detailed information about a specific tool by ID from the MCP service.

    Returns complete tool information including input schema, configuration,
    and execution details.
    """,
)
async def get_tool(
    tool_id: UUID,
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> ToolResponse:
    """Get tool from AI service MCP integration."""
    logger.info(
        "Getting MCP tool",
        extra={"tool_id": str(tool_id)}
    )

    project, _ = project_and_api_key
    result = await ai_client.get_mcp_tool(
        project_id=str(project.id),
        tool_id=str(tool_id),
    )

    return ToolResponse.model_validate(result)


@router.post(
    "/custom",
    response_model=ToolResponse,
    responses=CREATE_RESPONSES,
    status_code=status.HTTP_201_CREATED,
    summary="Create Custom Tool",
    description="""
    Create a new custom webhook tool in the MCP service via AI service.

    Custom tools allow you to integrate external services by providing a webhook URL
    that will be called when the tool is executed. The tool must include a JSON schema
    for input validation.
    """,
)
async def create_custom_tool(
    tool_data: CreateCustomToolRequest,
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> ToolResponse:
    """Create custom tool in AI service MCP integration."""
    logger.info(
        "Creating custom MCP tool",
        extra={
            "tool_name": tool_data.name,
            "tool_title": tool_data.title,
            "webhook_url": tool_data.webhook_url,
        }
    )

    project, _ = project_and_api_key
    result = await ai_client.create_custom_tool(
        project_id=str(project.id),
        tool_data=tool_data.model_dump(exclude_none=True),
    )

    return ToolResponse.model_validate(result)


@router.put(
    "/custom/{tool_id}",
    response_model=ToolResponse,
    responses=UPDATE_RESPONSES,
    summary="Update Custom Tool",
    description="""
    Update an existing custom webhook tool in the MCP service via AI service.

    Only custom tools can be updated. You can modify the webhook URL, input schema,
    timeout settings, and other configuration options.
    """,
)
async def update_custom_tool(
    tool_id: UUID,
    tool_data: UpdateCustomToolRequest,
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> ToolResponse:
    """Update custom tool in AI service MCP integration."""
    logger.info(
        "Updating custom MCP tool",
        extra={
            "tool_id": str(tool_id),
            "tool_title": tool_data.title,
            "webhook_url": tool_data.webhook_url,
        }
    )

    project, _ = project_and_api_key
    result = await ai_client.update_custom_tool(
        project_id=str(project.id),
        tool_id=str(tool_id),
        tool_data=tool_data.model_dump(exclude_none=True),
    )

    return ToolResponse.model_validate(result)


@router.delete(
    "/custom/{tool_id}",
    responses=DELETE_RESPONSES,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Custom Tool",
    description="""
    Delete a custom webhook tool from the MCP service via AI service.

    Only custom tools can be deleted. This action is irreversible and will
    also remove the tool from all projects where it was installed.
    """,
)
async def delete_custom_tool(
    tool_id: UUID,
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> None:
    """Delete custom tool from AI service MCP integration."""
    logger.info(
        "Deleting custom MCP tool",
        extra={"tool_id": str(tool_id)}
    )

    project, _ = project_and_api_key
    await ai_client.delete_custom_tool(
        project_id=str(project.id),
        tool_id=str(tool_id),
    )


@router.post(
    "/{tool_id}/execute",
    response_model=ToolExecutionResult,
    responses=CREATE_RESPONSES,
    status_code=status.HTTP_200_OK,
    summary="Execute Tool",
    description="""
    Execute a tool installed in the project via the MCP service through AI service.

    The tool must be installed and enabled in the current project. Provide input data
    according to the tool's input schema. The execution is asynchronous and returns
    immediately with execution details.
    """,
)
async def execute_tool(
    tool_id: UUID,
    execution_data: ExecuteToolRequest,
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> ToolExecutionResult:
    """Execute tool via AI service MCP integration."""
    logger.info(
        "Executing MCP tool",
        extra={
            "tool_id": str(tool_id),
            "input_data_keys": list(execution_data.input_data.keys()) if execution_data.input_data else [],
        }
    )

    project, _ = project_and_api_key
    result = await ai_client.execute_tool(
        project_id=str(project.id),
        tool_id=str(tool_id),
        execution_data=execution_data.model_dump(exclude_none=True),
    )

    return ToolExecutionResult.model_validate(result)
