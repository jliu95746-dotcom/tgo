import pytest
import httpx
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock
from uuid import uuid4
from fastapi import HTTPException
from app.services.provider_setup import build_model_request, run_model_probe, Connection
from app.schemas.provider_setup import ProviderModelProbe
from app.services.provider_setup import resolve_connection


def draft(kind='chat'):
    return ProviderModelProbe(provider='deepseek', api_base_url='https://example.test/v1', model_id='example', model_type=kind)


def test_probe_uses_inference_not_model_listing():
    url, headers, body = build_model_request(Connection('deepseek', 'https://example.test/v1', 'test-key', {}), draft())
    assert url.endswith('/chat/completions')
    assert body['model'] == 'example' and body['stream'] is False
    assert headers['Authorization'] == 'Bearer test-key'


def test_embedding_probe_calls_embedding_endpoint():
    url, _, body = build_model_request(Connection('custom', 'https://example.test/v1', 'test-key', {}), draft('embedding'))
    assert url.endswith('/embeddings') and body['input']


@pytest.mark.asyncio
async def test_http_success_without_model_output_is_not_success():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as client:
        result = await run_model_probe(Connection('deepseek', 'https://example.test/v1', 'test-key', {}), draft(), client)
    assert not result.success


@pytest.mark.asyncio
async def test_probe_reports_actual_output_and_redacts_upstream_errors():
    for response, expected in [(httpx.Response(200, json={'choices': [{'message': {'content': 'OK'}}]}), True),
                               (httpx.Response(401, text='test-secret'), False)]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: response)) as client:
            result = await run_model_probe(Connection('deepseek', 'https://example.test/v1', 'test-key', {}), draft(), client)
        assert result.success is expected
        assert 'test-secret' not in result.message


def test_creation_accepts_explicit_model_types_atomically():
    from app.schemas.ai_provider import AIProviderCreate
    payload = AIProviderCreate(provider='deepseek', name='test', api_key='test', available_models=[{'model_id': 'embed', 'model_type': 'embedding'}])
    assert payload.available_models[0].model_type == 'embedding'


def test_stored_secret_never_forwarded_to_changed_host():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(api_base_url='https://original.test/v1', provider='deepseek')
    payload = draft().model_copy(update={'provider_id': uuid4()})
    with pytest.raises(HTTPException) as error:
        resolve_connection(db, uuid4(), payload)
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_default_usage_blocks_removal_without_remote_lookup(monkeypatch):
    from app.services.provider_usage import assert_provider_unused, ai_client
    pid = uuid4()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(default_chat_provider_id=pid, default_chat_model='old')
    lookup = AsyncMock()
    monkeypatch.setattr(ai_client, 'list_agents', lookup)
    with pytest.raises(HTTPException) as error:
        await assert_provider_unused(db, uuid4(), pid)
    assert error.value.status_code == 409
    lookup.assert_not_called()


@pytest.mark.asyncio
async def test_employee_usage_blocks_removal_and_unavailable_lookup_fails_closed(monkeypatch):
    from app.services.provider_usage import assert_provider_unused, ai_client
    pid = uuid4()
    db = MagicMock(); db.query.return_value.filter.return_value.first.return_value = None
    for lookup, code in [(AsyncMock(return_value={'data': [{'llm_provider_id': str(pid), 'model': 'old'}]}), 409), (AsyncMock(side_effect=RuntimeError('offline')), 503)]:
        monkeypatch.setattr(ai_client, 'list_agents', lookup)
        with pytest.raises(HTTPException) as error:
            await assert_provider_unused(db, uuid4(), pid, {'old'})
        assert error.value.status_code == code
