"""Only current, complete owner requirements can enter the activation gate."""

from uuid import uuid4

import httpx
import pytest

from app.config import settings
from app.exceptions import AuthorizationError, ConflictError, ExternalServiceError
from app.schemas.agent_activation import (
    AgentActivationRequirements,
    AgentActivationRequirementsQuery,
)
from app.schemas.agent_trial_reviews import KnowledgeReviewCase
from app.services.api_service import APIServiceClient


def fixture():
    query = AgentActivationRequirementsQuery(
        project_id=uuid4(),
        requested_by=uuid4(),
        template_id="knowledge",
        template_version=1,
        channel="web",
    )
    cases = [
        KnowledgeReviewCase(
            id=f"knowledge.v1.{index + 1}",
            template_id="knowledge",
            template_version=1,
            question=question,
            expected_result=expected,
        )
        for index, (question, expected) in enumerate(
            [
                ("材质？", "grounded_answer"),
                ("容量？", "grounded_answer"),
                ("库存能确认吗？", "safe_refusal"),
            ]
        )
    ]
    value = AgentActivationRequirements.model_construct(
        **query.model_dump(),
        required_resources=["model", "knowledge"],
        required_capabilities=["governed_knowledge_search"],
        knowledge_cases=cases,
    )
    value.requirements_revision = value.definition_revision()
    return query, value.model_dump(mode="json")


@pytest.mark.asyncio
async def test_client_uses_private_route_trusted_key_and_never_caches(monkeypatch):
    query, body = fixture()
    received = []

    def respond(request):
        received.append(request)
        return (
            httpx.Response(200, json=body)
            if len(received) == 1
            else httpx.Response(403)
        )

    monkeypatch.setattr(settings, "secret_key", "synthetic-activation-test-key")
    client = APIServiceClient()
    client.internal_api_url = "http://owner/internal"
    client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        result = await client.get_agent_activation_requirements(query)
        assert result.project_id == query.project_id and not result.can_enable
        assert received[0].url.path == "/internal/ai/activation/requirements"
        assert (
            received[0].headers["X-Internal-API-Key"] == "synthetic-activation-test-key"
        )
        with pytest.raises(AuthorizationError):
            await client.get_agent_activation_requirements(query)
        assert len(received) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "project",
        "actor",
        "template",
        "version",
        "channel",
        "enable",
        "cases",
        "duplicate",
        "safety_only",
        "revision",
        "resource",
        "invalid_json",
        "unavailable",
        "redirect",
        "timeout",
        "forbidden",
        "old_version",
        "oversized",
    ],
)
async def test_unknown_forged_or_stale_owner_response_is_rejected(change):
    query, body = fixture()
    if change == "project":
        body["project_id"] = str(uuid4())
    elif change == "actor":
        body["requested_by"] = str(uuid4())
    elif change == "template":
        body["template_id"] = "aftersales"
    elif change == "version":
        body["template_version"] = 2
    elif change == "channel":
        body["channel"] = "wecom_kf"
    elif change == "enable":
        body["can_enable"] = True
    elif change == "cases":
        body["knowledge_cases"] = []
    elif change == "duplicate":
        body["knowledge_cases"].append(body["knowledge_cases"][0])
    elif change == "safety_only":
        body["knowledge_cases"] = body["knowledge_cases"][-1:]
    elif change == "revision":
        body["requirements_revision"] = "0" * 64
    elif change == "resource":
        body["required_resources"] = ["model"]

    def respond(request):
        if change == "oversized":
            return httpx.Response(200, content=b"x" * (256 * 1024 + 1))
        if change == "timeout":
            raise httpx.ReadTimeout("synthetic-private-marker", request=request)
        status = {
            "unavailable": 503,
            "redirect": 307,
            "forbidden": 403,
            "old_version": 409,
        }.get(change, 200)
        if change == "invalid_json":
            return httpx.Response(200, text="synthetic-private-marker")
        return httpx.Response(status, json=body)

    client = APIServiceClient()
    client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    error = (
        AuthorizationError
        if change == "forbidden"
        else ConflictError
        if change == "old_version"
        else ExternalServiceError
    )
    try:
        with pytest.raises(error) as failure:
            await client.get_agent_activation_requirements(query)
        assert "synthetic-private-marker" not in str(failure.value)
    finally:
        await client.aclose()
