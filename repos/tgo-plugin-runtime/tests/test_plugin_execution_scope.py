"""Tool requests must be scoped even when the caller knows a plugin ID."""

from unittest.mock import AsyncMock, Mock
import asyncio
import json
import struct

import httpx
import pytest
from fastapi import FastAPI

from app.api import routes
from app.services.plugin_manager import PluginConnection, plugin_manager


@pytest.mark.asyncio
@pytest.mark.parametrize("project,expected", [(None, 422), ("", 422), ("foreign", 404), ("owned", 200)])
async def test_tool_route_requires_owning_project(monkeypatch, project, expected):
    plugin = PluginConnection(id="fixture", name="fixture", version="1", project_id="owned")
    monkeypatch.setattr(plugin_manager, "_plugins", {"fixture": plugin})
    send = AsyncMock(return_value={"success": True, "content": "ok"})
    monkeypatch.setattr(plugin_manager, "send_request", send)
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/plugins/tools/execute/fixture/query",
                                     params={} if project is None else {"project_id": project},
                                     json={"arguments": {}, "context": {"visitor_id": "visitor"}})
    assert response.status_code == expected
    if expected == 200:
        assert send.await_args.kwargs["project_id"] == "owned"
        assert send.await_args.args[2]["visitor_id"] == "visitor"
    else:
        send.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_rechecks_project_before_writing(monkeypatch):
    writer = Mock()
    writer.is_closing.return_value = False
    plugin = PluginConnection(id="fixture", name="fixture", version="1", project_id="foreign", writer=writer)
    monkeypatch.setattr(plugin_manager, "_plugins", {"fixture": plugin})
    send = AsyncMock()
    monkeypatch.setattr(plugin_manager, "_send_message", send)
    assert await plugin_manager.send_request("fixture", "tool/execute", {}, project_id="owned") is None
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["owned", None])
async def test_scoped_route_delivers_context_over_plugin_transport(monkeypatch, owner):
    """Exercise the real dispatcher and framed TCP message, not a send mock."""
    received = asyncio.get_running_loop().create_future()
    finished = asyncio.Event()

    async def plugin_peer(reader, writer):
        try:
            size = struct.unpack(">I", await reader.readexactly(4))[0]
            message = json.loads(await reader.readexactly(size))
            received.set_result(message)
            reply = json.dumps({"id": message["id"], "result": {
                "success": True, "data": {"visitor_id": message["params"]["visitor_id"]},
            }}).encode()
            writer.write(struct.pack(">I", len(reply)) + reply)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    server = await asyncio.start_server(plugin_peer, "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
    plugin = PluginConnection(id="fixture", name="fixture", version="1", project_id=owner, writer=writer)
    monkeypatch.setattr(plugin_manager, "_plugins", {"fixture": plugin})

    async def receive_response():
        size = struct.unpack(">I", await reader.readexactly(4))[0]
        await plugin_manager.handle_response(plugin, json.loads(await reader.readexactly(size)))

    response_task = asyncio.create_task(receive_response())
    app = FastAPI()
    app.include_router(routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await asyncio.wait_for(client.post(
                "/plugins/tools/execute/fixture/query", params={"project_id": "owned"},
                json={"arguments": {"tracking_no": "FIXTURE"}, "context": {
                    "visitor_id": "visitor", "session_id": "session", "agent_id": "agent",
                }}), timeout=5)
        assert response.status_code == 200
        assert response.json()["data"] == {"visitor_id": "visitor"}
        message = await asyncio.wait_for(received, 2)
        assert message["method"] == "tool/execute"
        assert message["params"] == {
            "tool_name": "query", "arguments": {"tracking_no": "FIXTURE"},
            "visitor_id": "visitor", "session_id": "session", "agent_id": "agent", "language": None,
        }
        await asyncio.wait_for(response_task, 2)
        assert not plugin._pending_requests
    finally:
        response_task.cancel()
        await asyncio.gather(response_task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()
        await asyncio.wait_for(finished.wait(), 2)
