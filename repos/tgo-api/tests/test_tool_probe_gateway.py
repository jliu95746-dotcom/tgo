from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from app.api.v1.endpoints import ai_tools
from app.core.security import get_authenticated_project
from app.schemas.tool_probe import MCPDiscoverResponse


@pytest.mark.asyncio
async def test_probes_use_authenticated_project_and_actual_execution_client(monkeypatch):
    project = SimpleNamespace(id=uuid4())
    discover = AsyncMock(return_value=MCPDiscoverResponse(success=True, tools=[]))
    execute = AsyncMock(return_value={'success': False, 'error': 'HTTP 401'})
    monkeypatch.setattr(ai_tools.ai_client, 'discover_tools', discover)
    monkeypatch.setattr(ai_tools.ai_client, 'execute_tool', execute)
    app = FastAPI()
    app.include_router(ai_tools.router, prefix='/tools')
    app.dependency_overrides[get_authenticated_project] = lambda: (project, None)
    tool_id = uuid4()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/tools/discover', json={'endpoint': 'https://example.test/mcp', 'project_id': str(uuid4())})
        assert response.status_code == 200
        assert discover.await_args.args[0] == str(project.id)
        response = await client.post(f'/tools/{tool_id}/execute', json={'input_data': {'code': 'fixture'}, 'project_id': str(uuid4())})
        assert response.status_code == 200 and response.json()['success'] is False
        execute.assert_awaited_once_with(project_id=str(project.id), tool_id=str(tool_id), input_data={'code': 'fixture'})
        invalid = await client.post(f'/tools/{tool_id}/execute', json={'input_data': []})
        assert invalid.status_code == 422
