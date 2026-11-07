"""Response typing preserves pagination, tenant scope and upstream fields."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from app.models import AIProvider

from app.api.v1.endpoints import (
    ai_agents,
    ai_models,
    ai_providers,
    knowledge_versions,
    platforms,
    provider_setup,
    tags,
    visitor_waiting_queue,
)
from app.schemas import AIProviderListParams, PlatformListParams, TagListParams
from app.schemas.platform_models import (
    PlatformModelDefinition,
    StoredPlatformModel,
)
from app.schemas.provider_setup import ProviderConnectionDraft
from app.schemas.knowledge_versions import (
    VersionChangeRequest,
    VersionMaintenanceRequest,
)
from app.services import platform_models, shared_models
from app.services.provider_setup import Connection
from app.utils.crypto import encrypt_str


def empty_query(total):
    query = MagicMock()
    for method in [
        "filter",
        "join",
        "outerjoin",
        "options",
        "order_by",
        "offset",
        "limit",
    ]:
        getattr(query, method).return_value = query
    query.count.return_value = total
    query.all.return_value = []
    db = MagicMock()
    db.query.return_value = query
    db.scalar.return_value = total
    db.execute.return_value.all.return_value = []
    return db, query


@pytest.mark.parametrize(
    "endpoint",
    ["models", "providers", "platforms", "tags", "queue", "waiting"],
)
@pytest.mark.parametrize(
    "total,limit,offset",
    [(0, 2, 0), (5, 2, 0), (5, 2, 2), (5, 2, 4), (5, 2, 8)],
)
@pytest.mark.asyncio
async def test_list_pagination_and_project_scope_are_preserved(
    endpoint, total, limit, offset
):
    db, query = empty_query(total)
    staff = SimpleNamespace(project_id=uuid4(), username="synthetic-staff")
    common = {"db": db, "current_user": staff}
    if endpoint == "models":
        result = await ai_models.list_ai_models(
            model_type=None,
            is_active=True,
            limit=limit,
            offset=offset,
            **common,
        )
    elif endpoint == "providers":
        timestamp = datetime(2026, 1, 1, tzinfo=UTC)
        query.all.return_value = [
            AIProvider(
                id=uuid4(),
                project_id=staff.project_id,
                provider="openai",
                name=f"synthetic-{index}",
                api_key=None,
                config={},
                is_active=True,
                is_from_store=False,
                models=[],
                created_at=timestamp,
                updated_at=timestamp,
            )
            for index in range(total)
        ]
        result = await ai_providers.list_ai_providers(
            params=AIProviderListParams(limit=limit, offset=offset),
            model_type=None,
            **common,
        )
        assert len(result.data) == max(0, min(limit, total - offset))
    elif endpoint == "platforms":
        result = await platforms.list_platforms(
            params=PlatformListParams(limit=limit, offset=offset),
            x_user_language="zh",
            **common,
        )
    elif endpoint == "tags":
        result = await tags.list_tags(
            params=TagListParams(limit=limit, offset=offset),
            user_language="zh",
            **common,
        )
    elif endpoint == "queue":
        result = await visitor_waiting_queue.list_waiting_queue(
            status=None,
            source=None,
            urgency=None,
            visitor_id=None,
            limit=limit,
            offset=offset,
            **common,
        )
    else:
        result = await visitor_waiting_queue.list_waiting_only(
            urgency=None,
            limit=limit,
            offset=offset,
            **common,
        )
    expected = {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_next": offset + limit < total,
        "has_prev": offset > 0,
    }
    assert result.pagination.model_dump() == expected
    assert result.model_dump(mode="json")["pagination"] == expected
    assert isinstance(result.pagination.has_next, bool)
    assert isinstance(result.pagination.has_prev, bool)
    if endpoint == "models":
        statements = [db.execute.call_args.args[0]]
    else:
        statements = [
            arg for call in query.filter.call_args_list for arg in call.args
        ]
    compiled = " ".join(
        str(statement.compile(compile_kwargs={"literal_binds": True}))
        for statement in statements
    )
    assert "project_id" in compiled
    assert str(staff.project_id).replace("-", "") in compiled.replace("-", "")
    db.commit.assert_not_called()


@pytest.mark.parametrize("operation", ["list", "get"])
@pytest.mark.asyncio
async def test_agent_responses_preserve_upstream_extensions(
    monkeypatch, operation
):
    project_id, agent_id = uuid4(), uuid4()
    row = {
        "id": str(agent_id),
        "name": "synthetic",
        "extension": {"flag": True},
    }
    payload = (
        {"data": [row], "extension": [1, 2]} if operation == "list" else row
    )
    upstream = AsyncMock(return_value=payload)
    monkeypatch.setattr(
        ai_agents.ai_client,
        operation + "_agents" if operation == "list" else "get_agent",
        upstream,
    )
    enrich_many, enrich_one = AsyncMock(), AsyncMock()
    monkeypatch.setattr(ai_agents, "_enrich_agents_with_devices", enrich_many)
    monkeypatch.setattr(ai_agents, "_enrich_agent_with_device", enrich_one)
    project = (SimpleNamespace(id=project_id), "synthetic-project-key")
    if operation == "list":
        result = await ai_agents.list_agents(
            model=None,
            is_default=None,
            bound_device_id=None,
            limit=20,
            offset=0,
            project_and_api_key=project,
        )
        enrich_many.assert_awaited_once_with([row], str(project_id))
    else:
        result = await ai_agents.get_agent(
            agent_id,
            include_tools=True,
            include_collections=False,
            include_workflows=False,
            project_and_api_key=project,
        )
        enrich_one.assert_awaited_once_with(row, str(project_id))
    assert isinstance(result, JSONResponse)
    assert result.status_code == 200
    assert json.loads(result.body) == payload
    assert upstream.await_args.kwargs["project_id"] == str(project_id)


@pytest.mark.parametrize("enabled", [False, True])
def test_platform_media_model_selection_is_preserved(monkeypatch, enabled):
    secret = "synthetic-response-model-key"
    policy = StoredPlatformModel(
        version=1,
        definition=PlatformModelDefinition(
            provider_kind="openai", model="synthetic-base", active=True
        ),
        encrypted_api_key=encrypt_str(secret),
    )
    monkeypatch.setattr(
        platform_models, "stored_policy", lambda db: (None, policy)
    )
    defaults = {
        purpose: SimpleNamespace(model_id=purpose + "-synthetic")
        for purpose in ["chat", "embedding", "asr", "ocr", "vlm"]
    }
    monkeypatch.setattr(
        shared_models,
        "stored_shared_models",
        lambda db: SimpleNamespace(defaults=defaults) if enabled else None,
    )
    result = platform_models.runtime_model(Mock())
    assert result is not None
    assert result.model == "synthetic-base"
    assert result.api_key.get_secret_value() == secret
    assert secret not in repr(result)
    expected = {
        purpose: purpose + "-synthetic" for purpose in ["asr", "ocr", "vlm"]
    }
    assert result.approved_media_models == (expected if enabled else None)


@pytest.mark.parametrize("provider", ["ollama", "openai"])
@pytest.mark.asyncio
async def test_preview_models_preserves_request_and_result(
    monkeypatch, provider
):
    base = "https://models.example.test/v1"
    connection = Connection(provider, base, "synthetic-preview-key", {})
    monkeypatch.setattr(
        provider_setup, "resolve_connection", lambda *args: connection
    )
    payload = (
        {"models": [{"name": "synthetic-chat"}]}
        if provider == "ollama"
        else {"data": [{"id": "synthetic-chat", "object": "model"}]}
    )
    request = AsyncMock(return_value=httpx.Response(200, json=payload))
    manager = MagicMock()
    manager.__aenter__.return_value = SimpleNamespace(request=request)
    factory = Mock(return_value=manager)
    monkeypatch.setattr(provider_setup.httpx, "AsyncClient", factory)
    result = await provider_setup.preview_models(
        ProviderConnectionDraft(provider=provider, api_base_url=base),
        Mock(),
        SimpleNamespace(project_id=uuid4()),
    )
    assert result.provider == provider
    assert not result.is_fallback
    assert [model.id for model in result.models] == ["synthetic-chat"]
    assert "synthetic-preview-key" not in result.model_dump_json()
    factory.assert_called_once_with(timeout=15, follow_redirects=False)
    expected_url = (
        base.removesuffix("/v1") + "/api/tags"
        if provider == "ollama"
        else base + "/models"
    )
    headers = request.await_args.kwargs["headers"]
    assert request.await_args.args == ("GET", expected_url)
    assert headers == (
        {}
        if provider == "ollama"
        else {"Authorization": "Bearer synthetic-preview-key"}
    )


@pytest.mark.parametrize(
    "operation",
    ["history", "change", "restore", "disable", "cleanup", "enable"],
)
@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.asyncio
async def test_version_gateway_validates_responses_and_preserves_scope(
    monkeypatch, operation, valid
):
    project, reference, version_id = uuid4(), uuid4(), uuid4()
    user = SimpleNamespace(
        role="admin", project_id=project, username="synthetic-admin"
    )
    version = {
        "id": str(version_id),
        "number": 2,
        "state": "published",
        "author": "synthetic-author",
        "published_by": user.username,
        "published_at": "2026-01-01T00:00:00Z",
        "created_at": "2026-01-01T00:00:00Z",
        "error": None,
        "restored_from": None,
        "content": {"answer": "synthetic-answer"},
        "preview": "synthetic-preview",
    }
    if operation in {"history", "disable", "enable"}:
        payload = {
            "source_kind": "qa",
            "source_id": str(reference),
            "active_number": 2,
            "disabled": operation == "disable",
            "retention": 10,
            "versions": [version],
        }
    elif operation == "cleanup":
        payload = {
            "removable_numbers": [1],
            "removed": 1,
            "file_cleanup_pending": 0,
        }
    else:
        payload = version
    payload["upstream_only"] = "synthetic-private-extension"
    upstream = AsyncMock(return_value=payload if valid else {})
    monkeypatch.setattr(
        knowledge_versions.rag_client, "knowledge_version_request", upstream
    )
    path = f"/qa/{reference}"
    expected = ("POST", path, str(project), {"actor": user.username})

    async def invoke():
        nonlocal expected
        if operation == "history":
            expected = ("GET", path, str(project))
            return await knowledge_versions.history("qa", reference, user)
        if operation == "change":
            expected = (
                "POST",
                path,
                str(project),
                {"action": "publish", "actor": user.username},
            )
            return await knowledge_versions.change(
                "qa", reference, VersionChangeRequest(action="publish"), user
            )
        if operation == "restore":
            expected = (
                "POST",
                f"/versions/{version_id}/restore",
                str(project),
                {"actor": user.username},
            )
            return await knowledge_versions.transition(
                version_id, "restore", user
            )
        if operation == "cleanup":
            request = VersionMaintenanceRequest(
                retention=10, confirm=True, expected_numbers=[1]
            )
            expected = (
                "POST",
                path + "/cleanup",
                str(project),
                request.model_dump(mode="json"),
            )
            return await knowledge_versions.cleanup(
                "qa", reference, request, user
            )
        expected = (
            "POST",
            path + "/" + operation,
            str(project),
            {"actor": user.username},
        )
        return await getattr(knowledge_versions, operation)(
            "qa", reference, user
        )

    if not valid:
        with pytest.raises(ValidationError):
            await invoke()
    else:
        result = await invoke()
        assert "synthetic-private-extension" not in result.model_dump_json()
        if operation in {"history", "disable", "enable"}:
            assert result.source_id == reference
            assert result.disabled is (operation == "disable")
            assert result.versions[0].id == version_id
        elif operation == "cleanup":
            assert result.removed == 1
            assert result.removable_numbers == [1]
        else:
            assert result.id == version_id
            assert result.state == "published"
            assert result.content.answer == "synthetic-answer"
    upstream.assert_awaited_once_with(*expected)
