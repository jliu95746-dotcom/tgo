"""Only complete, current, scoped model-owner attestations can be consumed."""

from uuid import uuid4

import httpx
import pytest

from app.exceptions import AuthorizationError, ExternalServiceError
from app.schemas.agent_activation_models import AgentActivationModel, AgentActivationModelQuery
from app.services.api_service import APIServiceClient


def fixture():
    query = AgentActivationModelQuery(project_id=uuid4(), requested_by=uuid4(),
        provider_id=uuid4(), model="fixture-model")
    response = AgentActivationModel(**query.model_dump(), status="ready", effective_model="fixture-model",
        runtime_revision="a" * 64, owner_revision="b" * 64)
    return query, response.model_dump(mode="json")


@pytest.mark.asyncio
async def test_read_never_caches_model_or_role_and_uses_private_service_identity():
    query, body = fixture()
    calls = []

    def owner(request):
        calls.append(request)
        return httpx.Response(200, json=body) if len(calls) == 1 else httpx.Response(403)

    client = APIServiceClient()
    client.internal_api_url = "http://owner/internal"
    client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(owner))
    try:
        value = await client.get_agent_activation_model(query)
        assert value.status == "ready" and not value.can_enable
        assert calls[0].url.path == "/internal/ai/activation/model"
        assert calls[0].headers["X-Internal-API-Key"]
        with pytest.raises(AuthorizationError):
            await client.get_agent_activation_model(query)
        assert len(calls) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["project", "actor", "provider", "model", "effective_model", "approval", "scope",
    "missing_revision", "bad_revision", "credential", "unavailable", "redirect", "oversized"])
async def test_invalid_owner_identity_or_proof_is_rejected(changed):
    query, body = fixture()
    if changed in {"project", "actor", "provider"}:
        body[{"project": "project_id", "actor": "requested_by", "provider": "provider_id"}[changed]] = str(uuid4())
    elif changed == "model":
        body["model"] = "different-model"
    elif changed == "effective_model":
        body["effective_model"] = "different-model"
    elif changed == "approval":
        body["can_enable"] = True
    elif changed == "scope":
        body["scope"] = "employee_activation"
    elif changed == "missing_revision":
        body["owner_revision"] = None
    elif changed == "bad_revision":
        body["runtime_revision"] = "unknown"
    elif changed == "credential":
        body["api_key"] = "private-fixture-key"
    status = 503 if changed == "unavailable" else 302 if changed == "redirect" else 200
    response = httpx.Response(200, content=b"x" * (256 * 1024 + 1)) if changed == "oversized" else httpx.Response(status, json=body)
    client = APIServiceClient()
    client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response))
    try:
        with pytest.raises(ExternalServiceError):
            await client.get_agent_activation_model(query)
    finally:
        await client.aclose()
