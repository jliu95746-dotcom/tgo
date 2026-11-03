"""Public ingress preserves signed bytes and never trusts internal headers."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio

from app.api.v1.endpoints import workflow_webhook_ingress as public
from app.api.internal.endpoints import workflow_webhooks as private
from app.core.config import settings
from app.core.database import get_db
from app.schemas.webhook_delivery import WebhookAccepted
from app.schemas.webhook_authority import WebhookAuthority
from app.services.workflow_client import WorkflowServiceClient


def signature_headers():
    return {
        "X-TGO-Webhook-Timestamp": "1800000000",
        "X-TGO-Webhook-Event-ID": "synthetic-event",
        "X-TGO-Webhook-Key-ID": "synthetic-key",
        "X-TGO-Webhook-Signature": "sha256=" + "0" * 64,
        "Content-Type": "application/json",
    }


@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setattr(
        settings, "WEBHOOK_EXECUTION_ENABLED", True, raising=False
    )
    monkeypatch.setattr(public, "admit_public_webhook", AsyncMock())
    app = FastAPI()
    app.include_router(public.router, prefix="/v1/workflow-webhooks")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as value:
        yield value


@pytest.mark.asyncio
async def test_unauthenticated_route_forwards_exact_bytes_with_no_store(
    client, monkeypatch
):
    expected = WebhookAccepted(
        delivery_id=uuid4(), status="pending", deduplicated=False
    )
    upstream = AsyncMock(return_value=expected)
    monkeypatch.setattr(public.workflow_client, "accept_webhook", upstream)
    identifier = uuid4()
    body = b'{ "data" : {"question":"synthetic-private"} }\n'
    headers = signature_headers()
    headers["X-Internal-API-Key"] = "attacker-supplied-key"
    result = await client.post(
        "/v1/workflow-webhooks/" + str(identifier),
        content=body,
        headers=headers,
    )
    assert (
        result.status_code == 202
        and result.headers["cache-control"] == "no-store"
    )
    args = upstream.await_args.args
    assert args[0] == identifier and args[1] == body
    assert "X-Internal-API-Key" not in args[2]
    assert args[3] == "application/json"
    assert "synthetic-private" not in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,status",
    [("duplicate", 403), ("missing", 403), ("large", 413), ("encoding", 415)],
)
async def test_ambiguous_or_oversized_requests_never_reach_workflow(
    client, monkeypatch, case, status
):
    upstream = AsyncMock()
    monkeypatch.setattr(public.workflow_client, "accept_webhook", upstream)
    headers = list(signature_headers().items())
    body = b'{"data":{}}'
    if case == "duplicate":
        headers.append(("X-TGO-Webhook-Key-ID", "another"))
    elif case == "missing":
        headers = [
            (key, value)
            for key, value in headers
            if key != "X-TGO-Webhook-Key-ID"
        ]
    elif case == "large":
        body = b"x" * 65537
    else:
        headers.append(("Content-Encoding", "gzip"))
    result = await client.post(
        "/v1/workflow-webhooks/" + str(uuid4()), content=body, headers=headers
    )
    assert result.status_code == status
    upstream.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_public_gate_makes_no_upstream_or_rate_call(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "WEBHOOK_EXECUTION_ENABLED", False)
    upstream = AsyncMock()
    rate = AsyncMock()
    monkeypatch.setattr(public.workflow_client, "accept_webhook", upstream)
    monkeypatch.setattr(public, "admit_public_webhook", rate)
    result = await client.post(
        "/v1/workflow-webhooks/" + str(uuid4()), content=b"private"
    )
    assert result.status_code == 503
    upstream.assert_not_awaited()
    rate.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_authority_requires_key_and_echoes_fresh_binding(
    monkeypatch,
):
    monkeypatch.setattr(settings, "SECRET_KEY", "synthetic-authority-key")
    monkeypatch.setattr(settings, "WEBHOOK_EXECUTION_ENABLED", True)
    monkeypatch.setattr(
        private, "new_service_available", lambda db, project: True
    )
    db = Mock()
    db.scalar.return_value = uuid4()
    app = FastAPI()
    app.include_router(private.router, prefix="/internal/workflows/webhooks")
    app.dependency_overrides[get_db] = lambda: db
    body = WebhookAuthority(
        project_id=uuid4(),
        actor_staff_id=uuid4(),
        workflow_id="owned-flow",
        hook_id=uuid4(),
        binding_revision=2,
        release_id="fixed-release",
        release_digest="a" * 64,
    ).model_dump(mode="json")
    path = "/internal/workflows/webhooks/authority/verify"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.post(path, json=body)
        assert denied.status_code == 403
        allowed = await client.post(
            path,
            json=body,
            headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        assert allowed.status_code == 200 and allowed.json() == body
        db.scalar.return_value = None
        revoked = await client.post(
            path,
            json=body,
            headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        assert revoked.status_code == 409


@pytest.mark.asyncio
async def test_client_forwards_raw_body_and_server_selected_internal_key(
    monkeypatch,
):
    monkeypatch.setattr(settings, "SECRET_KEY", "synthetic-authority-key")
    client = WorkflowServiceClient()
    receipt = WebhookAccepted(
        delivery_id=uuid4(), status="pending", deduplicated=False
    )
    send = AsyncMock(
        return_value=httpx.Response(202, json=receipt.model_dump(mode="json"))
    )
    monkeypatch.setattr(client, "_make_request", send)
    hook = uuid4()
    values = dict(
        timestamp="1800000000",
        event_id="synthetic",
        key_id="sender",
        signature="sha256=" + "a" * 64,
    )
    result = await client.accept_webhook(
        hook, b'{ "data":{} }', values, "application/json"
    )
    assert result == receipt
    assert send.await_args.kwargs["raw_body"] == b'{ "data":{} }'
    assert (
        send.await_args.kwargs["extra_headers"]["X-Internal-API-Key"]
        == settings.SECRET_KEY
    )
    assert (
        send.await_args.kwargs["extra_headers"]["x-tgo-webhook-event-id"]
        == "synthetic"
    )
