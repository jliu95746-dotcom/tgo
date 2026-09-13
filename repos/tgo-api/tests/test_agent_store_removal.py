"""Recruitment is retired without removing local employees or other stores."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints import store
from app.schemas import store as store_schemas
from app.services.store_client import StoreClient


def test_recruitment_routes_absent_and_other_stores_retained(client):
    paths = client.get('/v1/openapi.json').json()['paths']
    assert '/v1/store/install-agent' not in paths
    assert '/v1/store/agent/{agent_id}/check-dependencies' not in paths
    assert '/v1/store/install-tool' not in paths
    for path in ['/v1/ai/agents', '/v1/store/install-model']:
        assert path in paths


def test_retired_schemas_and_client_methods_removed():
    for name in ['StoreAgentDetail', 'StoreToolSummary',
                 'AgentDependencyCheckResponse', 'StoreInstallAgentRequest']:
        assert not hasattr(store_schemas, name)
    for name in ['get_agent', 'install_agent', 'uninstall_agent']:
        assert not hasattr(StoreClient, name)
    assert not hasattr(StoreClient, 'install_tool')
    assert hasattr(StoreClient, 'install_model')


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['agents', 'agents/categories', 'agents/example/install', 'tools', 'tools/categories', 'install/tool/example'])
async def test_proxy_cannot_reopen_retired_employee_store(monkeypatch, path):
    def unexpected_request(**kwargs):
        pytest.fail('retired routes must not contact the store')

    monkeypatch.setattr(store.httpx, 'AsyncClient', unexpected_request)
    request = Request({'type': 'http', 'method': 'GET', 'headers': [], 'query_string': b''})
    with pytest.raises(HTTPException) as error:
        await store.proxy_store_request(path, request, SimpleNamespace())
    assert error.value.status_code == 410


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['models', 'auth/me'])
async def test_shared_store_proxy_still_forwards_other_resources(monkeypatch, path):
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    request = Request({'type': 'http', 'method': 'GET', 'headers': [], 'query_string': b''}, receive)
    client = AsyncMock()
    client.request.return_value = httpx.Response(200, json={'items': []})
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(store.httpx, 'AsyncClient', lambda **kwargs: context)
    result = await store.proxy_store_request(path, request, SimpleNamespace())
    assert result.status_code == 200
    assert client.request.await_args.kwargs['url'].endswith('/' + path)
