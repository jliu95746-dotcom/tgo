"""Plugin API endpoints - Proxies to tgo-plugin-runtime service."""

from datetime import datetime, timedelta, timezone
from typing import NoReturn

from fastapi import APIRouter, HTTPException, status, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import StreamingResponse
import httpx
from pydantic import JsonValue, TypeAdapter
from sqlalchemy.orm import Session
from jose import jwt

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import get_current_active_user, require_admin
from app.services.plugin_runtime_client import JsonObject, plugin_runtime_client
from app.services.plugin_progress import stream_plugin_progress
from app.models import Staff
from app.api.current_access_route import CurrentAccessRoute
from app.core.exceptions import TGOAPIException
from app.services.plugin_request_scope import capture_plugin_scope
from app.schemas.plugin import (
    PluginListResponse,
    PluginInfo,
    PluginRenderRequest,
    PluginEventRequest,
    VisitorPanelRenderRequest,
    VisitorPanelRenderResponse,
    ChatToolbarResponse,
    PluginRenderResponse,
    PluginActionResponse,
    PluginInstallRequest,
    InstalledPluginInfo,
    InstalledPluginListResponse,
    DevTokenRequest,
    DevTokenResponse,
    PluginFetchRequest,
    PluginFetchResponse,
    PluginUpdateCheckResponse,
    PluginUpgradeRequest,
)

logger = get_logger("api.plugins")
router = APIRouter(route_class=CurrentAccessRoute)
_plugin_error_body: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def _handle_runtime_error(e: Exception, context: str) -> NoReturn:
    """Helper to handle errors from plugin runtime service."""
    if isinstance(e, (HTTPException, TGOAPIException)):
        raise e
    if isinstance(e, httpx.HTTPStatusError):
        if 400 <= e.response.status_code < 500:
            try:
                data = _plugin_error_body.validate_json(e.response.content)
                detail = (
                    data.get("detail", "Plugin runtime request failed")
                    if isinstance(data, dict)
                    else "Plugin runtime request failed"
                )
            except ValueError:
                detail = "Plugin runtime request failed"
            raise HTTPException(
                status_code=e.response.status_code, detail=detail
            )
        logger.error(
            "Plugin runtime service error",
            extra={
                "status_code": e.response.status_code,
                "operation": context,
            },
        )
        raise HTTPException(
            status_code=502, detail="Plugin runtime service error"
        )
    if isinstance(e, httpx.RequestError):
        logger.error(
            "Plugin runtime connection error",
            extra={
                "operation": context,
                "error_type": type(e).__name__,
            },
        )
        raise HTTPException(
            status_code=502, detail="Plugin runtime service unavailable"
        )
    logger.error(
        "Unexpected plugin runtime error",
        extra={
            "operation": context,
            "error_type": type(e).__name__,
        },
    )
    raise HTTPException(status_code=500, detail="Internal server error")


# ==================== Plugin List ====================


@router.get("", response_model=PluginListResponse)
async def list_plugins(
    current_user: Staff = Depends(get_current_active_user),
) -> PluginListResponse:
    (
        'Get all registered plugins, including global and project-specific '
        'ones.'
    )
    try:
        data = await plugin_runtime_client.list_plugins(
            project_id=str(current_user.project_id)
        )
        return PluginListResponse(**data)
    except Exception as e:
        _handle_runtime_error(e, "list_plugins")


@router.get("/chat-toolbar/buttons", response_model=ChatToolbarResponse)
async def get_chat_toolbar_buttons(
    current_user: Staff = Depends(get_current_active_user),
) -> ChatToolbarResponse:
    (
        'Get all chat toolbar buttons from registered plugins for the '
        'current project.'
    )
    try:
        data = await plugin_runtime_client.get_chat_toolbar_buttons(
            project_id=str(current_user.project_id)
        )
        return ChatToolbarResponse(**data)
    except Exception as e:
        _handle_runtime_error(e, "get_chat_toolbar_buttons")


# ==================== Visitor Panel ====================


@router.post(
    "/visitor-panel/render", response_model=VisitorPanelRenderResponse
)
async def render_visitor_panels(
    request: VisitorPanelRenderRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
    current_user: Staff = Depends(get_current_active_user),
) -> VisitorPanelRenderResponse:
    """Render all visitor panel plugins for a specific visitor."""
    scope = await capture_plugin_scope(
        db,
        current_user,
        credentials,
        request.visitor_id,
        request.visitor,
        session_id=request.session_id,
        agent_id=None,
    )
    http_request.state.operation_guard = scope.guard

    # Enrich visitor info

    visitor_info = scope.visitor

    request_data = {
        "visitor_id": scope.visitor_id,
        "session_id": request.session_id,
        "visitor": visitor_info.model_dump(exclude_none=True)
        if visitor_info
        else None,
        "language": request.language,
        "context": request.context or {},
    }

    try:
        data = await plugin_runtime_client.render_visitor_panels(
            request_data, project_id=str(current_user.project_id)
        )
        scope.guard.check()
        return VisitorPanelRenderResponse(**data)
    except Exception as e:
        _handle_runtime_error(e, "render_visitor_panels")


# ==================== Chat Toolbar ====================


@router.post(
    "/chat-toolbar/{plugin_id}/render", response_model=PluginRenderResponse
)
async def render_chat_toolbar_plugin(
    plugin_id: str,
    request: PluginRenderRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
    current_user: Staff = Depends(get_current_active_user),
) -> PluginRenderResponse:
    """Render a chat toolbar plugin's content."""
    scope = await capture_plugin_scope(
        db,
        current_user,
        credentials,
        request.visitor_id,
        request.visitor,
        session_id=request.session_id,
        agent_id=request.agent_id,
    )
    http_request.state.operation_guard = scope.guard

    visitor_info = scope.visitor

    request_data = {
        "visitor_id": scope.visitor_id,
        "session_id": request.session_id,
        "visitor": visitor_info.model_dump(exclude_none=True)
        if visitor_info
        else None,
        "agent_id": request.agent_id,
        "action_id": request.action_id,
        "language": request.language,
        "context": request.context or {},
    }

    try:
        data = await plugin_runtime_client.render_chat_toolbar(
            plugin_id, request_data, project_id=str(current_user.project_id)
        )
        scope.guard.check()
        if data is None:
            raise HTTPException(
                status_code=404, detail=f"Plugin not found: {plugin_id}"
            )
        return PluginRenderResponse(**data)
    except HTTPException:
        raise
    except Exception as e:
        _handle_runtime_error(e, "render_chat_toolbar_plugin")


@router.post(
    "/chat-toolbar/{plugin_id}/event", response_model=PluginActionResponse
)
async def send_chat_toolbar_event(
    plugin_id: str,
    request: PluginEventRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
    current_user: Staff = Depends(get_current_active_user),
) -> PluginActionResponse:
    """Send an event to a chat toolbar plugin."""
    scope = await capture_plugin_scope(
        db,
        current_user,
        credentials,
        request.visitor_id,
        None,
        session_id=request.session_id,
        agent_id=None,
    )
    http_request.state.operation_guard = scope.guard

    request_data = {
        "event_type": request.event_type,
        "action_id": request.action_id,
        "extension_type": request.extension_type,
        "visitor_id": scope.visitor_id,
        "session_id": request.session_id,
        "selected_id": request.selected_id,
        "language": request.language,
        "form_data": request.form_data,
        "payload": request.payload or {},
    }

    try:
        data = await plugin_runtime_client.send_chat_toolbar_event(
            plugin_id, request_data, project_id=str(current_user.project_id)
        )
        scope.guard.check()
        if data is None:
            raise HTTPException(
                status_code=404, detail=f"Plugin not found: {plugin_id}"
            )
        return PluginActionResponse(**data)
    except HTTPException:
        raise
    except Exception as e:
        _handle_runtime_error(e, "send_chat_toolbar_event")


@router.get("/installed", response_model=InstalledPluginListResponse)
async def list_installed_plugins(
    current_user: Staff = Depends(get_current_active_user),
) -> InstalledPluginListResponse:
    """Get all installed plugins from database via runtime service."""
    try:
        data = await plugin_runtime_client.list_installed_plugins(
            project_id=str(current_user.project_id)
        )
        # Ensure latest_version and source_url are preserved by converting to
        # model

        return InstalledPluginListResponse(**data)
    except Exception as e:
        _handle_runtime_error(e, "list_installed_plugins")


@router.post("/fetch-info", response_model=PluginFetchResponse)
async def fetch_plugin_info(
    request: PluginFetchRequest,
    current_user: Staff = Depends(get_current_active_user),
) -> PluginFetchResponse:
    """Fetch plugin information from a URL (GitHub, Gitee, or custom)."""
    try:
        data = await plugin_runtime_client.fetch_plugin_info(request.url)
        if not data:
            raise HTTPException(
                status_code=400,
                detail=(
                    'Could not fetch plugin information from the provided URL'
                ),
            )
        return PluginFetchResponse(**data)
    except Exception as e:
        _handle_runtime_error(e, "fetch_plugin_info")


@router.post("/install-stream")
async def install_plugin_stream(
    request: PluginInstallRequest,
    current_user: Staff = Depends(get_current_active_user),
) -> StreamingResponse:
    """Install a plugin and stream progress via SSE proxy."""
    install_data = request.model_dump()
    install_data["project_id"] = str(current_user.project_id)
    return StreamingResponse(
        stream_plugin_progress("/plugins/install-stream", install_data),
        media_type="text/event-stream",
    )


@router.post("/install", response_model=InstalledPluginInfo)
async def install_plugin(
    request: PluginInstallRequest,
    current_user: Staff = Depends(get_current_active_user),
) -> InstalledPluginInfo:
    """Install a new plugin via runtime service."""
    # Add project_id to request for runtime to store

    request_data = request.model_dump(exclude_none=True)
    request_data["project_id"] = str(current_user.project_id)

    try:
        data = await plugin_runtime_client.install_plugin(request_data)
        if data is None:
            raise HTTPException(
                status_code=500, detail="Installation failed at runtime"
            )
        return InstalledPluginInfo(**data)
    except Exception as e:
        _handle_runtime_error(e, "install_plugin")


@router.get(
    "/{plugin_id}/check-update", response_model=PluginUpdateCheckResponse
)
async def check_plugin_update(
    plugin_id: str, current_user: Staff = Depends(get_current_active_user)
) -> PluginUpdateCheckResponse:
    """Check for plugin updates via runtime service."""
    try:
        data = await plugin_runtime_client.check_plugin_update(
            plugin_id, project_id=str(current_user.project_id)
        )
        if data is None:
            raise HTTPException(
                status_code=404, detail="Plugin update info not found"
            )
        return PluginUpdateCheckResponse(**data)
    except Exception as e:
        _handle_runtime_error(e, "check_plugin_update")


@router.post("/{plugin_id}/upgrade")
async def upgrade_plugin_stream(
    plugin_id: str,
    request: PluginUpgradeRequest,
    current_user: Staff = Depends(get_current_active_user),
) -> StreamingResponse:
    """Upgrade a plugin and stream progress via SSE proxy."""
    return StreamingResponse(
        stream_plugin_progress(
            f"/plugins/{plugin_id}/upgrade",
            request.model_dump(),
            params={"project_id": str(current_user.project_id)},
        ),
        media_type="text/event-stream",
    )


# ==================== Generic Plugin Routes ====================


@router.get("/{plugin_id}", response_model=PluginInfo)
async def get_plugin(
    plugin_id: str, current_user: Staff = Depends(get_current_active_user)
) -> PluginInfo:
    """Get a specific plugin by ID."""
    try:
        data = await plugin_runtime_client.get_plugin(
            plugin_id, project_id=str(current_user.project_id)
        )
        if data is None:
            raise HTTPException(
                status_code=404, detail=f"Plugin not found: {plugin_id}"
            )
        return PluginInfo(**data)
    except Exception as e:
        _handle_runtime_error(e, "get_plugin")


@router.post("/{plugin_id}/render", response_model=PluginRenderResponse)
async def render_plugin(
    plugin_id: str,
    request: PluginRenderRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
    current_user: Staff = Depends(get_current_active_user),
) -> PluginRenderResponse:
    """Trigger a plugin to render its UI."""
    scope = await capture_plugin_scope(
        db,
        current_user,
        credentials,
        request.visitor_id,
        request.visitor,
        session_id=request.session_id,
        agent_id=request.agent_id,
    )
    http_request.state.operation_guard = scope.guard

    visitor_info = scope.visitor

    request_data = {
        "visitor_id": scope.visitor_id,
        "session_id": request.session_id,
        "visitor": visitor_info.model_dump(exclude_none=True)
        if visitor_info
        else None,
        "agent_id": request.agent_id,
        "action_id": request.action_id,
        "language": request.language,
        "context": request.context or {},
    }

    try:
        data = await plugin_runtime_client.render_plugin(
            plugin_id, request_data, project_id=str(scope.access.project_id)
        )
        scope.guard.check()
        if data is None:
            raise HTTPException(
                status_code=404, detail=f"Plugin not found: {plugin_id}"
            )
        return PluginRenderResponse(**data)
    except Exception as e:
        _handle_runtime_error(e, "render_plugin")


@router.post("/{plugin_id}/event", response_model=PluginActionResponse)
async def send_plugin_event(
    plugin_id: str,
    request: PluginEventRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
    current_user: Staff = Depends(get_current_active_user),
) -> PluginActionResponse:
    """Send an event to a plugin."""
    scope = await capture_plugin_scope(
        db,
        current_user,
        credentials,
        request.visitor_id,
        None,
        session_id=request.session_id,
        agent_id=None,
    )
    http_request.state.operation_guard = scope.guard

    request_data = {
        "event_type": request.event_type,
        "action_id": request.action_id,
        "extension_type": request.extension_type,
        "visitor_id": scope.visitor_id,
        "session_id": request.session_id,
        "selected_id": request.selected_id,
        "language": request.language,
        "form_data": request.form_data,
        "payload": request.payload or {},
    }

    try:
        data = await plugin_runtime_client.send_plugin_event(
            plugin_id, request_data, project_id=str(scope.access.project_id)
        )
        scope.guard.check()
        if data is None:
            raise HTTPException(
                status_code=404, detail=f"Plugin not found: {plugin_id}"
            )
        return PluginActionResponse(**data)
    except Exception as e:
        _handle_runtime_error(e, "send_plugin_event")


@router.delete("/{plugin_id}", response_model=dict)
async def uninstall_plugin(
    plugin_id: str, current_user: Staff = Depends(get_current_active_user)
) -> JsonObject:
    """Uninstall a plugin via runtime service."""
    try:
        resp = await plugin_runtime_client.uninstall_plugin(
            plugin_id, project_id=str(current_user.project_id)
        )
        if resp and resp.get("success"):
            return resp
        raise HTTPException(
            status_code=400,
            detail=resp.get("message") if resp else "Uninstallation failed",
        )
    except Exception as e:
        _handle_runtime_error(e, "uninstall_plugin")


@router.post("/{plugin_id}/start", response_model=dict)
async def start_plugin(
    plugin_id: str, current_user: Staff = Depends(get_current_active_user)
) -> JsonObject:
    """Start a plugin via runtime service."""
    try:
        # Pass None or empty dict, runtime will fetch config from DB if not
        # provided

        resp = await plugin_runtime_client.start_plugin(
            plugin_id, None, project_id=str(current_user.project_id)
        )
        if resp and resp.get("success"):
            return resp
        raise HTTPException(
            status_code=400,
            detail=resp.get("message") if resp else "Failed to start",
        )
    except Exception as e:
        _handle_runtime_error(e, "start_plugin")


@router.post("/{plugin_id}/stop", response_model=dict)
async def stop_plugin(
    plugin_id: str, current_user: Staff = Depends(get_current_active_user)
) -> JsonObject:
    """Stop a plugin via runtime service."""
    try:
        resp = await plugin_runtime_client.stop_plugin(
            plugin_id, project_id=str(current_user.project_id)
        )
        if resp and resp.get("success"):
            return resp
        raise HTTPException(
            status_code=400,
            detail=resp.get("message") if resp else "Failed to stop",
        )
    except Exception as e:
        _handle_runtime_error(e, "stop_plugin")


@router.post("/{plugin_id}/restart", response_model=dict)
async def restart_plugin(
    plugin_id: str, current_user: Staff = Depends(get_current_active_user)
) -> JsonObject:
    """Restart a plugin via runtime service."""
    try:
        resp = await plugin_runtime_client.restart_plugin(
            plugin_id, project_id=str(current_user.project_id)
        )
        if resp and resp.get("success"):
            return resp
        raise HTTPException(
            status_code=400,
            detail=resp.get("message") if resp else "Failed to restart",
        )
    except Exception as e:
        _handle_runtime_error(e, "restart_plugin")


@router.get("/{plugin_id}/logs", response_model=dict)
async def get_plugin_logs(
    plugin_id: str, current_user: Staff = Depends(get_current_active_user)
) -> JsonObject:
    """Get plugin logs via runtime service."""
    try:
        data = await plugin_runtime_client.get_plugin_logs(
            plugin_id, project_id=str(current_user.project_id)
        )
        if data is None:
            raise HTTPException(
                status_code=404, detail=f"Plugin not found: {plugin_id}"
            )
        return data
    except Exception as e:
        _handle_runtime_error(e, "get_plugin_logs")


@router.post("/dev-token", response_model=DevTokenResponse)
async def generate_dev_token(
    request: DevTokenRequest, current_user: Staff = Depends(require_admin())
) -> DevTokenResponse:
    """Generate a dev token for plugin debugging in a specific project."""
    if request.project_id != current_user.project_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只能为当前所属项目生成插件调试令牌",
        )
    expires_at = datetime.now(timezone.utc) + timedelta(
        hours=request.expires_hours
    )
    token_data = {
        "project_id": str(current_user.project_id),
        "user_id": str(current_user.id),
        "type": "plugin_dev",
        "exp": expires_at,
    }

    token = jwt.encode(token_data, settings.SECRET_KEY, algorithm="HS256")

    return DevTokenResponse(token=token, expires_at=expires_at)
