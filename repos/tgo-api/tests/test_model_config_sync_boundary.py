"""Model configuration must have a matching receipt before reporting synced."""

import asyncio
from datetime import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from app.api.v1.endpoints import ai_providers
from app.models import AIProvider
from app.models.project_ai_config import ProjectAIConfig
from app.services import ai_provider_sync, project_ai_config_sync
from app.utils.crypto import encrypt_str
from tests import test_store_gateway_boundary as store_fixtures

records = store_fixtures.records
PRIVATE = "synthetic-private-model-config-sync"
SERVICES = [ai_provider_sync, project_ai_config_sync]


@pytest.fixture
def sync_records(records):
    db, projects = records
    ProjectAIConfig.__table__.create(db.get_bind())
    provider = AIProvider(
        project_id=projects[0].id, provider="openai", name="test-model",
        api_key=encrypt_str(PRIVATE), is_active=False,
    )
    config = ProjectAIConfig(
        project_id=projects[0].id, default_chat_model="test-chat",
        default_asr_model="test-asr", default_ocr_model="test-ocr",
        default_vlm_model="test-vlm",
    )
    db.add_all([provider, config])
    db.commit()
    return db, projects, provider, config


@pytest.fixture
def upstream(monkeypatch):
    original = httpx.AsyncClient
    state = SimpleNamespace(response=None, error=None, requests=[], on_request=None)

    def handle(request):
        state.requests.append(request)
        if state.on_request is not None:
            state.on_request()
        if state.error is not None:
            raise state.error
        return state.response

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return state


def item_for(service, sync_records):
    return sync_records[2 if service is ai_provider_sync else 3]


def receipt_for(service, items):
    field = "id" if service is ai_provider_sync else "project_id"
    return {"data": [{field: str(getattr(item, field))} for item in items]}


def sync_many(service):
    return (service.sync_providers if service is ai_provider_sync
            else service.sync_configs)


@pytest.mark.asyncio
@pytest.mark.parametrize("service", SERVICES)
async def test_matching_receipt_preserves_payload_and_headers(
    service, sync_records, upstream, monkeypatch,
):
    monkeypatch.setattr(service.settings, "AI_SERVICE_API_KEY", PRIVATE)
    monkeypatch.setattr(service.settings, "AI_SERVICE_URL", "http://ai.invalid/")
    item = item_for(service, sync_records)
    receipt = receipt_for(service, [item])
    receipt["future_field"] = {"values": [1, True, None]}
    upstream.response = httpx.Response(200, json=receipt)
    assert await sync_many(service)([item]) == (True, None, receipt)
    request, = upstream.requests
    assert request.headers["X-API-Key"] == PRIVATE
    body = json.loads(request.content)
    if service is ai_provider_sync:
        assert request.url.path == "/api/v1/llm-providers/sync"
        assert body["providers"][0]["api_key"] == PRIVATE
        assert body["providers"][0]["is_active"] is False
        assert body["providers"][0]["id"] == str(item.id)
    else:
        assert request.url.path == "/api/v1/project-ai-configs/sync"
        assert body["configs"][0]["default_asr_model"] == "test-asr"
        assert body["configs"][0]["default_ocr_model"] == "test-ocr"
        assert body["configs"][0]["default_vlm_model"] == "test-vlm"


@pytest.mark.asyncio
@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("body", [None, [], True, PRIVATE, {},
                                 {"data": None}, {"data": {}},
                                 {"data": []}, {"data": [None]},
                                 {"data": [{}]}, {"data": [{"id": 3}]},
                                 {"data": [{"project_id": str(uuid4()),
                                            "id": str(uuid4())}]}])
async def test_invalid_receipt_cannot_report_synced(
    service, sync_records, upstream, body,
):
    upstream.response = httpx.Response(200, content=json.dumps(body))
    ok, error, data = await sync_many(service)([item_for(service, sync_records)])
    assert ok is False and data is None
    assert error and PRIVATE not in error


@pytest.mark.asyncio
@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("status", [200, 204, 302, 400, 422, 500])
async def test_http_error_or_non_json_receipt_never_leaks_body(
    service, sync_records, upstream, status,
):
    upstream.response = httpx.Response(status, text=PRIVATE)
    ok, error, data = await sync_many(service)([item_for(service, sync_records)])
    assert ok is False and data is None
    assert error and PRIVATE not in error


@pytest.mark.asyncio
@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError,
                                       RuntimeError])
async def test_transport_errors_are_safe(service, sync_records, upstream,
                                         error_type):
    upstream.error = error_type(PRIVATE)
    ok, error, data = await sync_many(service)([item_for(service, sync_records)])
    assert ok is False and data is None
    assert error and PRIVATE not in error


@pytest.mark.asyncio
@pytest.mark.parametrize("service", SERVICES)
async def test_cancellation_is_not_converted_to_retry(service, sync_records,
                                                     upstream):
    upstream.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await sync_many(service)([item_for(service, sync_records)])
    assert len(upstream.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("service", SERVICES)
async def test_receipt_order_is_free_but_duplicates_cannot_replace_items(
    service, sync_records, upstream,
):
    item = item_for(service, sync_records)
    serialize = (service._provider_to_upsert if service is ai_provider_sync
                 else service._config_to_upsert)
    # A second persisted project/model configuration, not an invented receipt.
    db, projects, _, _ = sync_records
    if service is ai_provider_sync:
        other = AIProvider(project_id=projects[1].id, name="other",
                           provider="openai", api_key=encrypt_str("other-key"))
    else:
        other = ProjectAIConfig(project_id=projects[1].id)
    db.add(other)
    db.commit()
    assert serialize(other)
    body = receipt_for(service, [other, item])
    upstream.response = httpx.Response(200, json=body)
    assert await sync_many(service)([item, other]) == (True, None, body)
    upstream.response = httpx.Response(200, json=receipt_for(service, [item, item]))
    assert (await sync_many(service)([item, other]))[0] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("batch", [False, True])
async def test_retry_uses_same_identity_and_original_backoff(
    service, sync_records, upstream, monkeypatch, caplog, batch,
):
    item = item_for(service, sync_records)
    upstream.response = httpx.Response(502, text=PRIVATE)
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    if service is ai_provider_sync:
        retry = (service.sync_providers_with_retry if batch
                 else service.sync_provider_with_retry)
    else:
        retry = (service.sync_configs_with_retry if batch
                 else service.sync_config_with_retry)
    result = await retry([item] if batch else item, max_retries=2,
                         initial_delay=3)
    assert result[0] is False and PRIVATE not in result[1]
    assert len(upstream.requests) == 3
    assert len({request.content for request in upstream.requests}) == 1
    assert [call.args[0] for call in sleep.await_args_list] == [3, 6]
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("valid", [False, True])
async def test_saved_sync_status_requires_matching_receipt(
    service, sync_records, upstream, monkeypatch, valid,
):
    db = sync_records[0]
    item = item_for(service, sync_records)
    monkeypatch.setattr(service.settings, "AI_PROVIDER_SYNC_RETRY_COUNT", 0)
    monkeypatch.setattr(service.settings, "PROJECT_AI_CONFIG_SYNC_RETRY_COUNT", 0)
    upstream.response = httpx.Response(200, json=(
        receipt_for(service, [item]) if valid else {"private": PRIVATE}))
    update = (service.sync_provider_with_retry_and_update
              if service is ai_provider_sync
              else service.sync_config_with_retry_and_update)
    ok, error = await update(db, item)
    db.expire_all()
    persisted = db.get(type(item), item.id)
    assert ok is valid
    assert persisted.sync_status == ("synced" if valid else "failed")
    assert persisted.last_synced_at is not None
    assert PRIVATE not in (persisted.sync_error or "")
    assert (error is None) is valid


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["synced", "failed", "exception"])
async def test_enable_returns_masked_response_after_persisting_state(
    sync_records, monkeypatch, caplog, outcome,
):
    db, projects, provider, _ = sync_records

    async def sync(session, item):
        if outcome == "exception":
            raise RuntimeError(PRIVATE)
        item.sync_status = outcome
        item.sync_error = None if outcome == "synced" else "request_error"
        session.commit()
        return outcome == "synced", item.sync_error

    monkeypatch.setattr(ai_providers, "sync_provider_with_retry_and_update", sync)
    app = FastAPI()
    app.include_router(ai_providers.router, prefix="/providers")
    async def current_db():
        return db

    async def current_user():
        return SimpleNamespace(project_id=projects[0].id)

    app.dependency_overrides[ai_providers.get_db] = current_db
    app.dependency_overrides[ai_providers.get_current_active_user] = current_user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                base_url="http://test") as client:
        response = await client.post(f"/providers/{provider.id}/enable")
    assert response.status_code == 200
    assert response.json()["is_active"] is True
    assert response.json()["has_api_key"] is True
    assert PRIVATE not in response.text and PRIVATE not in caplog.text
    db.refresh(provider)
    assert provider.is_active is True
    if outcome != "exception":
        assert response.json()["sync_status"] == outcome


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["other-project", "deleted", "missing"])
async def test_enable_respects_original_project_and_deletion_filter(
    sync_records, monkeypatch, kind,
):
    db, projects, provider, _ = sync_records
    if kind == "deleted":
        provider.deleted_at = datetime.utcnow()
        db.commit()
    sync = AsyncMock()
    monkeypatch.setattr(ai_providers, "sync_provider_with_retry_and_update", sync)
    user = SimpleNamespace(project_id=projects[1 if kind == "other-project" else 0].id)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await ai_providers.enable_ai_provider(
            uuid4() if kind == "missing" else provider.id, db, user)
    assert exc.value.status_code == 404
    assert provider.is_active is False
    sync.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("service", SERVICES)
async def test_retry_recovers_with_matching_receipt(service, sync_records,
                                                   upstream, monkeypatch):
    item = item_for(service, sync_records)
    upstream.response = httpx.Response(502, text=PRIVATE)
    receipt = receipt_for(service, [item])

    async def recover(delay):
        upstream.response = httpx.Response(200, json=receipt)

    monkeypatch.setattr(asyncio, "sleep", recover)
    if service is ai_provider_sync:
        retry = service.sync_provider_with_retry
    else:
        retry = service.sync_config_with_retry
    assert await retry(item, max_retries=2, initial_delay=0) == (True, None, receipt)
    assert len(upstream.requests) == 2


@pytest.mark.asyncio
async def test_invalid_vendor_cannot_send_or_leak_configuration(
    sync_records, upstream,
):
    provider = sync_records[2]
    provider.config = {"vendor": {"private": PRIVATE}}
    ok, error, data = await ai_provider_sync.sync_provider(provider)
    assert ok is False and data is None
    assert error and PRIVATE not in error
    assert upstream.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("service", SERVICES)
async def test_receipt_matches_sent_identity_not_mutated_object(
    service, sync_records, upstream,
):
    item = item_for(service, sync_records)
    body = receipt_for(service, [item])
    upstream.response = httpx.Response(200, json=body)

    def mutate_after_send():
        field = "id" if service is ai_provider_sync else "project_id"
        setattr(item, field, uuid4())

    upstream.on_request = mutate_after_send
    assert await sync_many(service)([item]) == (True, None, body)
