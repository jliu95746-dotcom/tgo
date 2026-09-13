"""Record only dispatch metadata around the unchanged device MCP call."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from anyio import CancelScope
from fastapi import HTTPException
from pydantic import JsonValue

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.schemas.device_access import DeviceServicePrincipal
from app.schemas.device_session import StepStatus
from app.services.device_sessions import DeviceSessionService
from app.services.tcp_connection_manager import tcp_connection_manager

logger = get_logger(__name__)


async def record_device_tool(
    principal: DeviceServicePrincipal,
    device_id: str,
    tool_name: str,
    invoke: Callable[[], Awaitable[dict[str, JsonValue]]],
) -> dict[str, JsonValue]:
    device, session = UUID(device_id), principal.session_id
    if (
        principal.sub != "tgo-ai"
        or principal.device_id != device
        or session is None
    ):
        raise HTTPException(403, "Device execution scope mismatch")
    async with AsyncSessionLocal() as db:
        step = await DeviceSessionService(db).begin_step(
            principal.project_id, device, session, tool_name
        )
    status: StepStatus = "interrupted"
    screenshots = 0
    try:
        result = await invoke()
        failed = result.get("isError") is True or set(result) == {"error"}
        status = "failed" if failed else "completed"
        if failed and tcp_connection_manager.get_connection(device_id) is None:
            status = "interrupted"
        content = result.get("content")
        if isinstance(content, list):
            screenshots = sum(
                isinstance(item, dict) and item.get("type") == "image"
                for item in content
            )
        return result
    finally:
        # A cancelled HTTP stream must still attempt bounded durable cleanup.
        with CancelScope(shield=True):
            try:
                async with asyncio.timeout(5):
                    async with AsyncSessionLocal() as db:
                        await DeviceSessionService(db).end_step(
                            principal.project_id,
                            device,
                            session,
                            step,
                            status,
                            screenshots=screenshots,
                        )
            except Exception as exc:
                # The unfinished step prevents a false success later.
                logger.warning(
                    "Device step persistence failed: %s", type(exc).__name__
                )
