"""Recording preserves MCP payloads and refuses unauditable dispatches."""

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.schemas.device_access import DeviceServicePrincipal
from app.services import device_session_recording as module


@pytest.fixture
def recording(monkeypatch):
    principal = DeviceServicePrincipal(
        sub="tgo-ai", project_id=uuid4(), device_id=uuid4(), session_id=uuid4()
    )
    context = AsyncMock()
    context.__aenter__.return_value = Mock()
    monkeypatch.setattr(module, "AsyncSessionLocal", lambda: context)
    service = Mock(
        begin_step=AsyncMock(return_value=uuid4()), end_step=AsyncMock()
    )
    monkeypatch.setattr(module, "DeviceSessionService", lambda db: service)
    monkeypatch.setattr(
        module.tcp_connection_manager, "get_connection", lambda _: Mock()
    )
    return principal, service


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_recording_never_rewrites_tool_result(recording, failed):
    principal, service = recording
    result = {
        "isError": failed,
        "content": [{"type": "text", "text": "owned-secret-not-recorded"}],
        "structuredContent": {"value": 42},
    }
    invoke = AsyncMock(return_value=result)
    assert (
        await module.record_device_tool(
            principal, str(principal.device_id), "fixture", invoke
        )
        is result
    )
    assert service.end_step.call_args.args[-1] == (
        "failed" if failed else "completed"
    )
    assert "owned-secret" not in str(service.mock_calls)


@pytest.mark.asyncio
async def test_missing_record_refuses_to_dispatch(recording):
    principal, service = recording
    service.begin_step.side_effect = RuntimeError("database unavailable")
    invoke = AsyncMock()
    with pytest.raises(RuntimeError):
        await module.record_device_tool(
            principal, str(principal.device_id), "fixture", invoke
        )
    invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_tool_is_recorded_as_interrupted(recording):
    principal, service = recording
    invoke = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await module.record_device_tool(
            principal, str(principal.device_id), "fixture", invoke
        )
    assert service.end_step.call_args.args[-1] == "interrupted"


@pytest.mark.asyncio
async def test_cleanup_failure_preserves_result(recording, caplog):
    principal, service = recording
    service.end_step.side_effect = RuntimeError("private-record-failure")
    result = {"content": [{"type": "text", "text": "actual result"}]}
    observed = await module.record_device_tool(
        principal, str(principal.device_id), "fixture",
        AsyncMock(return_value=result),
    )
    assert observed is result
    assert "private-record-failure" not in caplog.text
