"""MCP Project Tools endpoints - proxy to AI service MCP integration."""

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.common_responses import (
    CREATE_RESPONSES,
    CRUD_RESPONSES,
    DELETE_RESPONSES,
    UPDATE_RESPONSES,
)
from app.core.security import get_authenticated_project
from app.core.security import AuthenticatedProject
from app.schemas.mcp import (
    BulkInstallRequest,
    InstallToolRequest,
    ProjectToolListResponse,
    ProjectToolResponse,
    ProjectToolStats,
    UpdateProjectToolRequest,
)
from app.services.ai_client import ai_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "",
    response_model=ProjectToolListResponse,
    responses=CRUD_RESPONSES,
    summary="List Project Tools",
    description=(
        '\n'
        '    Retrieve a paginated list of tools installed in the project via '
        'AI service MCP integration.\n'
        '    \n'
        '    Returns detailed information about each tool installation '
        'including configuration,\n'
        '    status, and associated tool details.\n'
        '    '
    ),
)
async def list_project_tools(
    source_type: Optional[str] = Query(
        None, description="Filter by tool source type (MCP_SERVER, CUSTOM)"
    ),
    status: Optional[str] = Query(
        None,
        description="Filter by tool status (ACTIVE, INACTIVE, DEPRECATED)",
    ),
    is_enabled: Optional[bool] = Query(
        None, description="Filter by enabled status"
    ),
    search: Optional[str] = Query(
        None, description="Search term for tool name or description"
    ),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> ProjectToolListResponse:
    """List project tools from AI service MCP integration."""
    project, _ = project_and_api_key
    logger.info(
        "Listing MCP project tools",
        extra={
            "source_type": source_type,
            "status": status,
            "is_enabled": is_enabled,
            "search": search,
            "page": page,
            "per_page": per_page,
        },
    )

    result = await ai_client.list_project_tools(
        project_id=str(project.id),
        source_type=source_type,
        status=status,
        is_enabled=is_enabled,
        search=search,
        page=page,
        per_page=per_page,
    )

    return ProjectToolListResponse.model_validate(result)


@router.get(
    "/stats",
    response_model=ProjectToolStats,
    responses=CRUD_RESPONSES,
    summary="Get Project Tool Statistics",
    description=(
        '\n'
        '    Retrieve statistics for tool installations in the project via '
        'AI service MCP integration.\n'
        '\n'
        '    Returns comprehensive statistics including total installations, '
        'enabled/disabled counts,\n'
        '    tool source type breakdown, and recent activity metrics.\n'
        '    '
    ),
)
async def get_project_tool_stats(
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> ProjectToolStats:
    """Get project tool statistics from AI service MCP integration."""
    logger.info("Getting MCP project tool statistics")

    project, _ = project_and_api_key
    result = await ai_client.get_project_tool_stats(
        project_id=str(project.id),
    )

    return ProjectToolStats.model_validate(result)


@router.get(
    "/{tool_id}",
    response_model=ProjectToolResponse,
    responses=CRUD_RESPONSES,
    summary="Get Project Tool",
    description=(
        '\n'
        '    Retrieve detailed information about a specific tool '
        'installation in the project.\n'
        '\n'
        '    Returns complete information about the tool installation '
        'including configuration,\n'
        '    status, and full tool details.\n'
        '    '
    ),
)
async def get_project_tool(
    tool_id: UUID,
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> ProjectToolResponse:
    """Get project tool from AI service MCP integration."""
    logger.info("Getting MCP project tool", extra={"tool_id": str(tool_id)})

    project, _ = project_and_api_key
    result = await ai_client.get_project_tool(
        project_id=str(project.id),
        tool_id=str(tool_id),
    )

    return ProjectToolResponse.model_validate(result)


@router.put(
    "/{tool_id}",
    response_model=ProjectToolResponse,
    responses=UPDATE_RESPONSES,
    summary="Update Project Tool",
    description=(
        '\n'
        '    Update a tool installation configuration via AI service MCP '
        'integration.\n'
        '    \n'
        "    You can modify the tool's enabled status and project-specific "
        'configuration.\n'
        '    Changes take effect immediately for future tool executions.\n'
        '    '
    ),
)
async def update_project_tool(
    tool_id: UUID,
    tool_data: UpdateProjectToolRequest,
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> ProjectToolResponse:
    """Update project tool in AI service MCP integration."""
    logger.info(
        "Updating MCP project tool",
        extra={
            "tool_id": str(tool_id),
            "is_enabled": tool_data.is_enabled,
            "has_configuration": tool_data.configuration is not None,
        },
    )

    project, _ = project_and_api_key
    result = await ai_client.update_project_tool(
        project_id=str(project.id),
        tool_id=str(tool_id),
        tool_data=tool_data.model_dump(exclude_none=True),
    )

    return ProjectToolResponse.model_validate(result)


@router.delete(
    "/{tool_id}",
    responses=DELETE_RESPONSES,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Uninstall Tool",
    description=(
        '\n'
        '    Remove a tool installation from the project via AI service MCP '
        'integration.\n'
        '    \n'
        '    This will disable the tool and remove it from the project. The '
        'tool itself\n'
        '    remains available for installation in other projects.\n'
        '    '
    ),
)
async def uninstall_tool(
    tool_id: UUID,
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> None:
    """Uninstall tool from AI service MCP integration."""
    logger.info(
        "Uninstalling MCP project tool", extra={"tool_id": str(tool_id)}
    )

    project, _ = project_and_api_key
    await ai_client.uninstall_tool(
        project_id=str(project.id),
        tool_id=str(tool_id),
    )


@router.post(
    "/install",
    response_model=ProjectToolResponse,
    responses=CREATE_RESPONSES,
    status_code=status.HTTP_201_CREATED,
    summary="Install Tool",
    description=(
        '\n'
        '    Install a tool in the project via AI service MCP integration.\n'
        '    \n'
        '    The tool will be made available for execution within the '
        'project with the\n'
        '    specified configuration. You can optionally disable the tool '
        'after installation.\n'
        '    '
    ),
)
async def install_tool(
    install_data: InstallToolRequest,
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> ProjectToolResponse:
    """Install tool via AI service MCP integration."""

    project, _ = project_and_api_key
    result = await ai_client.install_tool(
        project_id=str(project.id),
        install_data=install_data.model_dump(exclude_none=True),
    )

    return ProjectToolResponse.model_validate(result)


@router.post(
    "/bulk-install",
    response_model=List[ProjectToolResponse],
    responses=CREATE_RESPONSES,
    status_code=status.HTTP_201_CREATED,
    summary="Bulk Install Tools",
    description=(
        '\n'
        '    Install multiple tools in the project at once via AI service '
        'MCP integration.\n'
        '    \n'
        '    This is more efficient than installing tools individually. All '
        'tools will use\n'
        '    the same default configuration and enabled status unless '
        'overridden.\n'
        '    '
    ),
)
async def bulk_install_tools(
    bulk_install_data: BulkInstallRequest,
    project_and_api_key: AuthenticatedProject = Depends(
        get_authenticated_project
    ),
) -> List[ProjectToolResponse]:
    """Bulk install tools via AI service MCP integration."""
    logger.info(
        "Bulk installing MCP tools",
        extra={
            "project_id": str(bulk_install_data.project_id),
            "tool_count": len(bulk_install_data.tool_ids),
            "enable_all": bulk_install_data.enable_all,
            (
                'has_default_configuration'
            ): bulk_install_data.default_configuration
            is not None,
        },
    )

    project, _ = project_and_api_key
    result = await ai_client.bulk_install_tools(
        project_id=str(project.id),
        bulk_install_data=bulk_install_data.model_dump(exclude_none=True),
    )

    return [ProjectToolResponse.model_validate(item) for item in result]
