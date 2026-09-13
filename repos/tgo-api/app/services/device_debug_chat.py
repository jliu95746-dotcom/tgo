"""Reuse the AI runtime with a fixed target and truthful SSE events."""

import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from uuid import UUID

from pydantic import JsonValue, TypeAdapter, ValidationError

from app.core.exceptions import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.schemas.device_debug_chat import (
    DebugAgentBinding,
    DebugDeviceBinding,
    DeviceDebugChatRequest,
)
from app.services.ai_client import ai_client
from app.services.device_control_client import device_control_client

logger = get_logger(__name__)
_json_value: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_events = frozenset({"connected", "event", "error", "disconnected"})


async def validate_device_debug(
    request: DeviceDebugChatRequest, project_id: str
) -> None:
    device_data = await device_control_client.get_device(
        str(request.device_id), project_id
    )
    if not device_data:
        raise NotFoundError("设备")
    try:
        device = DebugDeviceBinding.model_validate(device_data)
    except ValidationError as exc:
        raise ExternalServiceError("device-control", "设备信息格式无效") from exc
    if device.id != request.device_id or device.project_id != UUID(project_id):
        raise NotFoundError("设备")
    if device.status != "online":
        raise ConflictError("设备离线，请连接后再调试")
    agent_data = await ai_client.get_agent(
        project_id, str(request.agent_id), include_tools=False
    )
    try:
        agent = DebugAgentBinding.model_validate(agent_data)
    except ValidationError as exc:
        raise ExternalServiceError("ai", "AI 员工信息格式无效") from exc
    if agent.id != request.agent_id:
        raise NotFoundError("AI 员工")
    if agent.bound_device_id != request.device_id:
        raise ConflictError("所选 AI 员工未绑定此设备，请确认后再调试")
    if request.model is not None and request.model != agent.model:
        raise ConflictError("调试使用 AI 员工已配置的模型，请先在员工设置中修改")


def _encode(event: str, data: JsonValue) -> str:
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def stream_device_debug(
    request: DeviceDebugChatRequest, project_id: str
) -> AsyncIterator[str]:
    source = ai_client.run_supervisor_agent_stream(
        message=request.message,
        project_id=project_id,
        agent_id=str(request.agent_id),
        enable_memory=False,
        system_message=request.system_prompt,
        expected_device_id=str(request.device_id),
    )
    try:
        async with aclosing(source):
            async for event, raw in source:
                if event not in _events:
                    raise ValueError("Unexpected SSE event")
                data = _json_value.validate_python(raw)
                if not isinstance(data, dict):
                    raise ValueError("Invalid SSE object")
                if event == "disconnected":
                    break
                terminal = event == "error" or (
                    event == "event"
                    and data.get("event_type")
                    in {"workflow_completed", "workflow_failed"}
                )
                yield _encode(event, data)
                if terminal:
                    return
        yield _encode(
            "error",
            {"event_type": "error", "error": "调试连接中断，未收到执行结果，请先确认设备状态"},
        )
    except Exception as exc:
        # Do not expose credentials, internal addresses or raw exception text.
        logger.warning(
            "Device debug stream failed",
            extra={"error_type": type(exc).__name__},
        )
        yield _encode(
            "error",
            {"event_type": "error", "error": "设备调试失败，请检查 AI 员工配置和设备连接"},
        )
