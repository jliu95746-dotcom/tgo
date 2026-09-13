"""Exercise saved tool changes across fresh sessions and a real HTTP target."""

import json
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import tools
from app.database import get_db
from app.dependencies import get_current_or_internal_project_id
from app.exceptions import NotFoundError
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from app.runtime.tools.builder.agent_builder import AgentBuilder
from app.runtime.tools.config import ToolsRuntimeSettings
from app.schemas.agent import AgentCreate, AgentToolCreate, AgentUpdate
from app.services.agent_service import AgentService


@pytest.fixture
def tool_target() -> Iterator[tuple[str, list[tuple[str, str, str]]]]:
    calls: list[tuple[str, str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.reply(body["tracking_no"])

        def do_GET(self) -> None:
            self.reply(parse_qs(urlparse(self.path).query)["tracking_no"][0])

        def reply(self, tracking_no: str) -> None:
            path = urlparse(self.path).path
            calls.append((self.command, path, self.headers["X-Fixture-Version"]))
            output = json.dumps({"path": path, "tracking_no": tracking_no}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(
        target=lambda: server.serve_forever(poll_interval=0.05), daemon=True,
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("use_default", [False, True])
async def test_saved_edit_disable_unbind_and_delete_reach_runtime(
    db_session: AsyncSession,
    tool_target: tuple[str, list[tuple[str, str, str]]],
    use_default: bool,
) -> None:
    base_url, calls = tool_target
    project_id = uuid4()
    session_factory = async_sessionmaker(
        db_session.bind, expire_on_commit=False,
    )

    async def fresh_session():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.include_router(tools.router)
    app.dependency_overrides[get_db] = fresh_session
    app.dependency_overrides[get_current_or_internal_project_id] = lambda: project_id
    builder = AgentBuilder(ToolsRuntimeSettings())

    async def runtime_functions(agent_id: UUID):
        async with session_factory() as session:
            adapter = AIServiceClient(AgentService(session), project_id)
            agent = (
                await adapter.get_default_agent({}) if use_default
                else await adapter.get_agent(str(agent_id), {})
            )
            return await builder._build_mcp_tools_from_agent(agent, None, None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        config = {
            "method": "POST", "timeout": 3,
            "headers": {"X-Fixture-Version": "original"},
            "parameters": [{
                "name": "tracking_no", "type": "string", "required": True,
                "description": "Original parameter description",
            }],
        }
        created = await client.post("/tools", json={
            "project_id": str(project_id), "name": "saved_lookup",
            "description": "Original tool description", "tool_type": "FUNCTION",
            "transport_type": "http_webhook", "endpoint": f"{base_url}/original",
            "config": config,
        })
        assert created.status_code == 200
        tool_id = UUID(created.json()["id"])
        query = {"project_id": str(project_id)}
        listed = await client.get("/tools", params=query)
        assert listed.status_code == 200
        assert listed.json()[0]["config"] == config

        async with session_factory() as session:
            agent = await AgentService(session).create_agent(project_id, AgentCreate(
                name="Isolated lifecycle employee", model="fixture-model",
                is_default=True, tools=[AgentToolCreate(tool_id=tool_id)],
            ))
            agent_id = agent.id

        functions = await runtime_functions(agent_id)
        assert len(functions) == 1
        first = await functions[0].entrypoint(tracking_no="TEST-BEFORE")
        assert json.loads(first) == {"path": "/original", "tracking_no": "TEST-BEFORE"}

        config["method"] = "GET"
        config["headers"] = {"X-Fixture-Version": "edited"}
        config["parameters"][0]["description"] = "Edited parameter description"
        changed = await client.patch(f"/tools/{tool_id}", params=query, json={
            "name": "edited_lookup", "description": "Edited tool description",
            "endpoint": f"{base_url}/edited", "config": config,
        })
        assert changed.status_code == 200
        listed = await client.get("/tools", params=query)
        assert listed.json()[0]["config"] == config
        functions = await runtime_functions(agent_id)
        assert len(functions) == 1
        assert functions[0].name == "edited_lookup"
        assert functions[0].description == "Edited tool description"
        assert functions[0].parameters["properties"]["tracking_no"][
            "description"
        ] == "Edited parameter description"
        second = await functions[0].entrypoint(tracking_no="TEST-AFTER")
        assert json.loads(second) == {"path": "/edited", "tracking_no": "TEST-AFTER"}
        assert calls == [("POST", "/original", "original"), ("GET", "/edited", "edited")]

        cleared = await client.patch(
            f"/tools/{tool_id}", params=query, json={"description": None},
        )
        assert cleared.status_code == 200
        listed = await client.get("/tools", params=query)
        assert listed.json()[0]["description"] is None
        assert listed.json()[0]["name"] == "edited_lookup"
        assert listed.json()[0]["config"] == config
        assert len(await runtime_functions(agent_id)) == 1

        async with session_factory() as session:
            await AgentService(session).set_tool_enabled(
                project_id, agent_id, tool_id, False,
            )
        assert await runtime_functions(agent_id) == []
        async with session_factory() as session:
            await AgentService(session).set_tool_enabled(
                project_id, agent_id, tool_id, True,
            )
        assert len(await runtime_functions(agent_id)) == 1

        async with session_factory() as session:
            await AgentService(session).update_agent(
                project_id, agent_id, AgentUpdate(tools=[]),
            )
        assert await runtime_functions(agent_id) == []
        async with session_factory() as session:
            await AgentService(session).update_agent(
                project_id, agent_id,
                AgentUpdate(tools=[AgentToolCreate(tool_id=tool_id)]),
            )
        assert len(await runtime_functions(agent_id)) == 1

        foreign_query = {"project_id": str(uuid4())}
        assert (await client.get("/tools", params=foreign_query)).json() == []
        assert (await client.patch(
            f"/tools/{tool_id}", params=foreign_query, json={"name": "unauthorized"},
        )).status_code == 404
        assert (await client.delete(
            f"/tools/{tool_id}", params=foreign_query,
        )).status_code == 404
        assert len(await runtime_functions(agent_id)) == 1

        removed = await client.delete(f"/tools/{tool_id}", params=query)
        assert removed.status_code == 200
        assert removed.json()["deleted_at"] is not None
        assert (await client.get("/tools", params=query)).json() == []
        assert await runtime_functions(agent_id) == []
        rejected = await client.post(f"/tools/{tool_id}/execute", json={
            "input_data": {"tracking_no": "TEST-DELETED"},
        })
        assert rejected.status_code == 404
        async with session_factory() as session:
            with pytest.raises(NotFoundError):
                await AgentService(session).update_agent(
                    project_id, agent_id,
                    AgentUpdate(tools=[AgentToolCreate(tool_id=tool_id)]),
                )
        assert len(calls) == 2
