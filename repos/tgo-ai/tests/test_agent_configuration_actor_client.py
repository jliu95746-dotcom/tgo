"""Fresh private configuration access cannot depend on a working template or model."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from app.config import settings
from app.exceptions import AuthorizationError, ExternalServiceError
from app.schemas.agent_configuration_actor import (
    AgentConfigurationActor,
    AgentConfigurationActorQuery,
)
from app.services.api_service import APIServiceClient


def fixture():
    query = AgentConfigurationActorQuery(project_id=uuid4(), requested_by=uuid4())
    receipt = AgentConfigurationActor(
        **query.model_dump(), checked_at=datetime.now(UTC)
    )
    return query, receipt.model_dump(mode="json")


@pytest.mark.asyncio
async def test_configuration_actor_read_is_private_fresh_and_uncached(monkeypatch):
    query, body = fixture()
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json=body) if len(seen) == 1 else httpx.Response(403)

    monkeypatch.setattr(settings, "secret_key", "synthetic-actor-private-key")
    client = APIServiceClient()
    client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        receipt = await client.get_agent_configuration_actor(query)
        assert (
            receipt.requested_by == query.requested_by and receipt.can_enable is False
        )
        with pytest.raises(AuthorizationError):
            await client.get_agent_configuration_actor(query)
        assert len(seen) == 2 and seen[0].url.path == "/internal/ai/activation/actor"
        assert seen[0].headers["X-Internal-API-Key"] == "synthetic-actor-private-key"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "scope",
        "project",
        "actor",
        "can_enable",
        "authorized",
        "old",
        "future",
        "naive",
        "extra",
        "missing_scope",
        "missing_authorized",
        "missing_can_enable",
    ],
)
async def test_invalid_or_old_configuration_actor_receipt_is_rejected(fault):
    query, body = fixture()
    if fault == "scope":
        body["scope"] = "employee_activation"
    if fault == "project":
        body["project_id"] = str(uuid4())
    if fault == "actor":
        body["requested_by"] = str(uuid4())
    if fault == "can_enable":
        body["can_enable"] = True
    if fault == "authorized":
        body["authorized"] = False
    if fault == "old":
        body["checked_at"] = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    if fault == "future":
        body["checked_at"] = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    if fault == "naive":
        body["checked_at"] = datetime.now().isoformat()
    if fault == "extra":
        body["credential"] = "must-not-be-returned"
    if fault.startswith("missing_"):
        body.pop(fault.removeprefix("missing_"))
    client = APIServiceClient()
    client._http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    try:
        with pytest.raises(ExternalServiceError) as error:
            await client.get_agent_configuration_actor(query)
        assert "must-not-be-returned" not in str(error.value)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 302, 404, 500])
async def test_configuration_actor_transport_failures_never_grant_access(status):
    query, _ = fixture()
    client = APIServiceClient()
    client._http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status))
    )
    try:
        expected = AuthorizationError if status in {401, 403} else ExternalServiceError
        with pytest.raises(expected):
            await client.get_agent_configuration_actor(query)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_configuration_actor_transport_timeout_and_size_are_bounded():
    query, _ = fixture()

    def timeout(request):
        raise httpx.ReadTimeout("private detail must stay internal", request=request)

    for respond in [
        timeout,
        lambda _: httpx.Response(200, content=b" " * (256 * 1024 + 1)),
    ]:
        client = APIServiceClient()
        client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            with pytest.raises(ExternalServiceError) as error:
                await client.get_agent_configuration_actor(query)
            assert "private detail" not in str(error.value)
        finally:
            await client.aclose()
