"""AI Tools endpoints - proxy to AI service."""

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.security import get_authenticated_project
from app.schemas.tools import ToolCreateRequest, ToolResponse, ToolType, ToolUpdateRequest
from app.services.ai_client import ai_client
from app.schemas.tool_probe import MCPDiscoverRequest, MCPDiscoverResponse, ToolTestRequest, ToolTestResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/discover", response_model=MCPDiscoverResponse)
async def discover_tools(
    request: MCPDiscoverRequest,
    project_and_api_key=Depends(get_authenticated_project),
) -> MCPDiscoverResponse:
    project, _ = project_and_api_key
    return await ai_client.discover_tools(str(project.id), request)


@router.post("/{tool_id}/execute", response_model=ToolTestResponse)
async def test_saved_tool(
    tool_id: UUID,
    request: ToolTestRequest,
    project_and_api_key=Depends(get_authenticated_project),
) -> ToolTestResponse:
    project, _ = project_and_api_key
    result = await ai_client.execute_tool(
        project_id=str(project.id), tool_id=str(tool_id), input_data=request.input_data,
    )
    return ToolTestResponse.model_validate(result)


@router.get("", response_model=List[ToolResponse])
async def list_tools(
    tool_type: Optional[ToolType] = Query(None, description="Filter by tool type (MCP, FUNCTION, or ALL)"),
    include_deleted: bool = Query(False, description="Include soft-deleted tools"),
    project_and_api_key=Depends(get_authenticated_project),
) -> List[ToolResponse]:
    """
    List tools for the current project.
    
    Proxies to AI service endpoint: GET /api/v1/tools
    """
    project, _ = project_and_api_key
    
    # Map 'ALL' to None for downstream service
    effective_tool_type = None if tool_type == ToolType.ALL else (tool_type.value if tool_type else None)
    
    logger.info(
        f"Listing tools for project {project.id}",
        extra={
            "project_id": str(project.id),
            "tool_type": tool_type,
            "effective_tool_type": effective_tool_type,
            "include_deleted": include_deleted,
        }
    )
    
    result = await ai_client.list_tools(
        project_id=str(project.id),
        tool_type=effective_tool_type,
        include_deleted=include_deleted,
    )
    
    return result


@router.post("", response_model=ToolResponse, status_code=201)
async def create_tool(
    tool_data: ToolCreateRequest,
    project_and_api_key=Depends(get_authenticated_project),
) -> ToolResponse:
    """
    Create a new tool for the current project.

    Proxies to AI service endpoint: POST /api/v1/tools
    """
    project, _ = project_and_api_key

    logger.info(
        f"Creating tool for project {project.id}",
        extra={
            "project_id": str(project.id),
            "tool_name": tool_data.name,
            "tool_type": tool_data.tool_type,
        }
    )

    # Add project_id from authenticated context
    tool_data_dict = tool_data.model_dump(exclude_none=True)
    tool_data_dict["project_id"] = str(project.id)

    result = await ai_client.create_tool(
        tool_data=tool_data_dict
    )

    return result


@router.patch("/{tool_id}", response_model=ToolResponse)
async def update_tool(
    tool_id: UUID,
    tool_data: ToolUpdateRequest,
    project_and_api_key=Depends(get_authenticated_project),
) -> ToolResponse:
    """
    Update an existing tool.

    Proxies to AI service endpoint: PATCH /api/v1/tools/{tool_id}
    """
    project, _ = project_and_api_key

    logger.info(
        f"Updating tool {tool_id} for project {project.id}",
        extra={
            "project_id": str(project.id),
            "tool_id": str(tool_id),
            "update_fields": list(tool_data.model_dump(exclude_unset=True).keys()),
        }
    )

    # Omitted fields stay unchanged; explicit null clears nullable metadata.
    tool_data_dict = tool_data.model_dump(exclude_unset=True)

    result = await ai_client.update_tool(
        project_id=str(project.id),
        tool_id=str(tool_id),
        tool_data=tool_data_dict,
    )

    return result


@router.delete("/{tool_id}", response_model=ToolResponse)
async def delete_tool(
    tool_id: UUID,
    project_and_api_key=Depends(get_authenticated_project),
) -> ToolResponse:
    """
    Delete a tool (soft delete).

    Proxies to AI service endpoint: DELETE /api/v1/tools/{tool_id}
    """
    project, _ = project_and_api_key

    logger.info(
        f"Deleting tool {tool_id} for project {project.id}",
        extra={
            "project_id": str(project.id),
            "tool_id": str(tool_id),
        }
    )

    result = await ai_client.delete_tool(
        project_id=str(project.id),
        tool_id=str(tool_id),
    )

    return result

