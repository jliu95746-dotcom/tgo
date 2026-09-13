"""HTTP tool failures must not expose transport credentials to the model."""
import asyncio
import json
import logging
from typing import cast
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.runtime.tools.utils import create_http_tool
from app.services.tool_executor import ToolExecutor
from tests.test_bound_http_tool_runtime import bound_agent


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["runtime", "executor"])
@pytest.mark.parametrize(
    "failure", [401, 500, "timeout", "connection", "unexpected", "cancelled", "success"]
)
async def test_http_failure_has_safe_diagnostics_without_raw_payload(
    monkeypatch, caplog, path, failure
):
    caplog.set_level(logging.INFO, logger="httpx")
    agent, tool = bound_agent()
    secret = "fixture-secret-never-forward"
    tool.endpoint = f"https://example.test/lookup?api_key={secret}"
    tool.config = {**tool.config, "headers": {"Authorization": f"Bearer {secret}"}}

    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout(secret, request=request)
        if failure == "connection":
            raise httpx.ConnectError(secret, request=request)
        if failure == "unexpected":
            raise ValueError(secret)
        if failure == "cancelled":
            raise asyncio.CancelledError()
        if failure == "success":
            return httpx.Response(
                200, json={"tracking_no": "fixture-001", "status": "in_transit"}
            )
        return httpx.Response(failure, text=f"internal error: {secret}")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    if path == "runtime":
        function = create_http_tool(
            tool.name, tool.description, tool.endpoint, headers=tool.config["headers"]
        )
        invoke = lambda: function.entrypoint(tracking_no="fixture-001")
    else:
        executor = ToolExecutor(cast(AsyncSession, Mock()), agent.project_id)
        invoke = lambda: executor._execute_http(tool, {"tracking_no": "fixture-001"})
    if failure == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await invoke()
        return
    output = await invoke()
    assert secret not in caplog.text
    assert secret not in output
    assert "example.test" not in output
    if failure == "success":
        assert json.loads(output) == {
            "tracking_no": "fixture-001",
            "status": "in_transit",
        }
    else:
        assert output.startswith("<error>") and output.endswith("</error>")
        if isinstance(failure, int):
            assert str(failure) in output
        if failure == "timeout":
            assert "Timeout" in output or "超时" in output


def test_transport_filter_is_idempotent_and_keeps_warning_diagnostics(caplog):
    from app.core.logging import setup_logging, TransportDetailFilter

    setup_logging()
    setup_logging()
    logger = logging.getLogger("httpcore.http11")
    caplog.set_level(logging.DEBUG, logger=logger.name)
    logger.debug("headers Authorization: fixture-secret")
    logger.warning("connection unavailable")
    assert "fixture-secret" not in caplog.text
    assert "connection unavailable" in caplog.text
    assert sum(isinstance(item, TransportDetailFilter) for item in logger.filters) == 1
