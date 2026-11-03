"""Plugin Tools proxy endpoints - Proxies to tgo-plugin-runtime service."""


from fastapi import APIRouter, HTTPException, Depends, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import get_current_active_user
from app.services.plugin_runtime_client import plugin_runtime_client
from app.models.staff import Staff
from app.api.current_access_route import CurrentAccessRoute
from app.core.exceptions import TGOAPIException
from app.services.plugin_request_scope import capture_plugin_scope
from app.schemas.plugin import (
    ToolExecuteRequest,
    ToolExecuteResponse,
)

logger = get_logger("api.plugin_tools")
router = APIRouter(route_class=CurrentAccessRoute)


@router.post("/execute/{plugin_id}/{tool_name}", response_model=ToolExecuteResponse)
async def execute_plugin_tool(
    plugin_id: str,
    tool_name: str,
    request: ToolExecuteRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
) -> ToolExecuteResponse:
    """
    Proxy an MCP tool execution request to tgo-plugin-runtime.
    
    Staff-facing execution; project identity is derived from authentication.
    """
    if not current_user.project_id:
        raise HTTPException(status_code=403, detail="Project access required")
    scope = await capture_plugin_scope(db, current_user, credentials, request.context.visitor_id,
        session_id=request.context.session_id, agent_id=request.context.agent_id)
    http_request.state.operation_guard = scope.guard
    visitor_info = scope.visitor
    # Build request for plugin-runtime (enriched with visitor data)
    request_data = {
        "arguments": request.arguments,
        "context": {
            "visitor_id": scope.visitor_id,
            "session_id": request.context.session_id,
            "agent_id": request.context.agent_id,
            "language": request.context.language,
        },
    }
    
    # Add enriched visitor info
    if visitor_info:
        request_data["context"]["visitor"] = visitor_info.model_dump(exclude_none=True)

    try:
        result = await plugin_runtime_client.execute_tool(
            plugin_id, tool_name, request_data, project_id=str(scope.access.project_id),
        )
        scope.guard.check()
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plugin not found: {plugin_id}"
            )
        return ToolExecuteResponse(**result)
    except (HTTPException, TGOAPIException):
        raise
    except Exception as e:
        logger.error(f"Failed to execute plugin tool: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Plugin runtime service unavailable"
        )
