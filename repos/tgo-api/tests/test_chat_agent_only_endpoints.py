"""Tests for agent-only public chat flows."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from app.schemas.visitor_data_access import VisitorDataAccess

from app.api.v1.endpoints import chat as chat_endpoints
from app.api.v1.endpoints import platforms as platform_endpoints
from app.schemas.chat import ChatCompletionRequest, StaffAgentChatRequest
from app.schemas.platform_schema import PlatformCreate
from app.models.platform import PlatformType
from app.schemas.employee_style import EmployeeStyle
from app.services.ai_client import ai_client


@pytest.fixture(autouse=True)
def isolate_employee_settings(monkeypatch):
    monkeypatch.setattr(chat_endpoints, "resolve_employee_style", AsyncMock(
        side_effect=lambda _project, agent_id, *_args, **_kwargs: EmployeeStyle(agent_id=agent_id)))
    isolate_visitor_transport(monkeypatch)

    class _IntentStub:
        def __init__(self, _db):
            pass

        async def analyze_text_message(self, **_kwargs):
            return SimpleNamespace(
                routing_target="auto_reply", tool_context=None,
                excluded_tool_ids=(), handoff_result=None,
                contextual_reply=None,
            )

    monkeypatch.setattr(chat_endpoints, "MessageIntentOrchestrator", _IntentStub)


def isolate_visitor_transport(monkeypatch):
    """These unit cases exercise employee routing; real holder checks have separate HTTP fixtures."""
    async def resolve(db, platform, from_uid, *_args, **kwargs):
        visitor, changed = await chat_endpoints.get_or_create_visitor(db=db,platform=platform,
            platform_open_id=from_uid,nickname=kwargs.get("nickname"),avatar_url=kwargs.get("avatar_url"))
        access = VisitorDataAccess(visitor_id=visitor.id,project_id=visitor.project_id,platform_id=platform.id,
            platform_api_key=SecretStr("owned-unit-platform"),im_token=SecretStr("owned-unit-holder"))
        return visitor, changed, access

    async def guarded(factory, _access=None):
        return await factory()

    monkeypatch.setattr(chat_endpoints,"resolve_chat_visitor",resolve)
    monkeypatch.setattr(chat_endpoints,"guarded_chat_call",guarded)


class _NoOpDB:
    """Small DB double for chat endpoint unit tests."""

    def add(self, _obj: object) -> None:
        return None

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


class _PlatformDB:
    """DB double that records newly created platforms."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def query(self, *_args):
        query = MagicMock()
        query.filter.return_value.first.return_value = None
        return query

    def commit(self) -> None:
        return None

    def refresh(self, _obj: object, attribute_names=None) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("service_mode", ["auto", "assist", "manual"])
async def test_explicit_handoff_bypasses_model_and_queues_human(
    monkeypatch, stream, service_mode,
):
    project_id = uuid4()
    platform = SimpleNamespace(id=uuid4(), agent_id=None, type="wecom")
    project = SimpleNamespace(id=project_id)
    visitor = SimpleNamespace(id=uuid4(), project_id=project_id)
    monkeypatch.setattr(
        chat_endpoints.chat_service, "validate_platform_and_project",
        lambda *_args: (platform, project),
    )
    monkeypatch.setattr(chat_endpoints, "get_or_create_visitor", AsyncMock(
        return_value=(visitor, False),
    ))
    monkeypatch.setattr(
        chat_endpoints.chat_service, "resolve_service_mode",
        lambda *_args: service_mode,
    )
    monkeypatch.setattr(chat_endpoints.chat_service,
                        "send_user_message_to_wukongim", AsyncMock())
    handoff = AsyncMock(return_value={"request_id": "handoff-1", "status": "pending"})
    monkeypatch.setattr(chat_endpoints, "request_human_handoff", handoff,
                        raising=False)
    classifier = MagicMock(side_effect=AssertionError("model classification used"))
    monkeypatch.setattr(chat_endpoints, "MessageIntentOrchestrator", classifier)
    transfer = AsyncMock()
    monkeypatch.setattr(chat_endpoints, "transfer_to_staff", transfer)

    result = await chat_endpoints.chat_completion(
        ChatCompletionRequest(
            api_key="fixture", message="转人工", from_uid="external-user",
            source_message_id="source-42", stream=stream,
        ),
        request=SimpleNamespace(state=SimpleNamespace()), db=_NoOpDB(),
    )

    assert handoff.await_args.kwargs["source_message_id"] == "source-42"
    assert handoff.await_args.kwargs["strict_source_receipt"] is True
    classifier.assert_not_called()
    transfer.assert_not_called()
    if stream:
        chunks = [chunk async for chunk in result.body_iterator]
        assert len(chunks) == 1 and '"event_type": "human_handoff"' in chunks[0]
    else:
        assert result["event_type"] == "human_handoff"
        assert result["handoff"]["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("handoff_failure", [False, True])
async def test_intent_pipeline_error_stops_ai_and_requests_human(
    monkeypatch, caplog, stream, handoff_failure,
):
    project_id = uuid4()
    platform = SimpleNamespace(id=uuid4(), agent_id=None, type="wecom")
    visitor = SimpleNamespace(
        id=uuid4(), project_id=project_id, is_unassigned=True,
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service, "validate_platform_and_project",
        lambda *_args: (platform, SimpleNamespace(id=project_id)),
    )
    monkeypatch.setattr(
        chat_endpoints, "get_or_create_visitor",
        AsyncMock(return_value=(visitor, False)),
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service, "resolve_service_mode",
        lambda *_args: "auto",
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service, "send_user_message_to_wukongim",
        AsyncMock(),
    )
    classifier = MagicMock()
    classifier.return_value.analyze_text_message = AsyncMock(
        side_effect=RuntimeError("private-provider-marker")
    )
    monkeypatch.setattr(chat_endpoints, "MessageIntentOrchestrator", classifier)
    handoff = AsyncMock(
        return_value={"request_id": "handoff-2", "status": "pending"},
        side_effect=RuntimeError("private-handoff-marker")
        if handoff_failure else None,
    )
    monkeypatch.setattr(chat_endpoints, "request_human_handoff", handoff)
    transfer = AsyncMock(side_effect=AssertionError("AI path continued"))
    monkeypatch.setattr(chat_endpoints, "transfer_to_staff", transfer)

    request = ChatCompletionRequest(
        api_key="fixture", message="咨询商品", from_uid="external-user",
        source_message_id="source-43", stream=stream,
    )
    if handoff_failure and not stream:
        with pytest.raises(HTTPException) as error:
            await chat_endpoints.chat_completion(
                request, request=SimpleNamespace(state=SimpleNamespace()),
                db=_NoOpDB(),
            )
        assert error.value.status_code == 503
        assert error.value.detail["routing_reason"] == "intent_pipeline_unavailable"
        result = None
    else:
        result = await chat_endpoints.chat_completion(
            request, request=SimpleNamespace(state=SimpleNamespace()),
            db=_NoOpDB(),
        )

    handoff.assert_awaited_once()
    assert handoff.await_args.kwargs["strict_source_receipt"] is True
    assert handoff.await_args.kwargs["routing_reason"] == "intent_pipeline_unavailable"
    transfer.assert_not_awaited()
    assert "private-provider-marker" not in caplog.text
    assert "private-handoff-marker" not in caplog.text
    if stream:
        chunks = [chunk async for chunk in result.body_iterator]
        assert len(chunks) == 1
        expected_event = "error" if handoff_failure else "human_handoff"
        assert f'"event_type": "{expected_event}"' in chunks[0]
    elif not handoff_failure:
        assert result["event_type"] == "human_handoff"

@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("background", [False, True])
async def test_paused_employee_acknowledges_without_starting_reply(
    monkeypatch, stream, background,
):
    from app.services.employee_style import InactiveEmployeeError

    project_id = uuid4()
    platform = SimpleNamespace(
        id=uuid4(), agent_id=None, ai_mode="auto", type="wecom",
    )
    visitor = SimpleNamespace(
        id=uuid4(), project_id=project_id, is_unassigned=True,
        ai_disabled=None, is_last_message_from_ai=False,
        is_last_message_from_visitor=True, last_client_msg_no=None,
        humanization_skill_name=None, humanization_skill_enabled=False,
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service, "validate_platform_and_project",
        lambda *_args: (platform, SimpleNamespace(id=project_id)),
    )
    monkeypatch.setattr(chat_endpoints, "get_or_create_visitor", AsyncMock(
        return_value=(visitor, False),
    ))
    monkeypatch.setattr(chat_endpoints.chat_service,
                        "send_user_message_to_wukongim", AsyncMock())
    monkeypatch.setattr(chat_endpoints, "transfer_to_staff", AsyncMock(
        return_value=SimpleNamespace(success=True, assigned_staff_id=uuid4()),
    ))
    monkeypatch.setattr(chat_endpoints.wukongim_client,
                        "send_visitor_profile_updated", AsyncMock())
    monkeypatch.setattr(chat_endpoints.chat_service, "is_ai_disabled",
                        lambda *_args: False)
    resolver = AsyncMock(side_effect=InactiveEmployeeError("AI 客服已停用"))
    monkeypatch.setattr(chat_endpoints, "resolve_employee_style", resolver)
    claim = MagicMock()
    monkeypatch.setattr(chat_endpoints, "claim_ai_interaction", claim)
    result = await chat_endpoints.chat_completion(
        ChatCompletionRequest(
            api_key="fixture", message="产品介绍", from_uid="fixture-visitor",
            stream=stream, wukongim_only=background,
        ), request=SimpleNamespace(state=SimpleNamespace()), db=_NoOpDB(),
    )
    claim.assert_not_called()
    assert resolver.await_args.kwargs["require_active"] is True
    if stream:
        chunks = [chunk async for chunk in result.body_iterator]
        assert len(chunks) == 1 and '"event_type": "ai_disabled"' in chunks[0]
    else:
        assert result["event_type"] == "ai_disabled" and not result["success"]


@pytest.mark.asyncio
async def test_chat_completion_prefers_platform_agent_id(monkeypatch) -> None:
    """Visitor chat should forward the platform-level agent override."""

    platform_agent_id = uuid4()
    project_id = uuid4()
    assigned_staff_id = uuid4()
    visitor = SimpleNamespace(
        project_id=project_id, humanization_skill_name=None, humanization_skill_enabled=False,
        id=uuid4(),
        is_unassigned=True,
        ai_disabled=None,
        is_last_message_from_ai=False,
        is_last_message_from_visitor=True,
        last_client_msg_no=None,
    )
    platform = SimpleNamespace(
        id=uuid4(),
        agent_id=platform_agent_id,
        ai_mode="auto",
        type="wecom",
    )
    project = SimpleNamespace(id=project_id, api_key="ak_project")
    handle_ai_mock = AsyncMock(return_value={"success": True, "content": "ok"})

    monkeypatch.setattr(
        chat_endpoints.chat_service,
        "validate_platform_and_project",
        lambda _api_key, _db: (platform, project),
    )
    monkeypatch.setattr(
        chat_endpoints,
        "get_or_create_visitor",
        AsyncMock(return_value=(visitor, False)),
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service,
        "send_user_message_to_wukongim",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        chat_endpoints,
        "transfer_to_staff",
        AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                assigned_staff_id=assigned_staff_id,
                message="assigned",
                queue_position=None,
            )
        ),
    )
    monkeypatch.setattr(
        chat_endpoints.wukongim_client,
        "send_visitor_profile_updated",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service,
        "is_ai_disabled",
        lambda _platform, _visitor: False,
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service,
        "handle_ai_response_non_stream",
        handle_ai_mock,
    )
    interaction_run = SimpleNamespace(
        id=uuid4(),
        response_client_msg_no="ai_test",
        status="running",
    )
    monkeypatch.setattr(
        chat_endpoints,
        "claim_ai_interaction",
        lambda *_args, **_kwargs: SimpleNamespace(
            run=interaction_run,
            is_duplicate=False,
        ),
    )
    monkeypatch.setattr(
        chat_endpoints,
        "mark_ai_interaction_finished",
        lambda *_args, **_kwargs: None,
    )

    result = await chat_endpoints.chat_completion(
        ChatCompletionRequest(
            api_key="pk_test",
            message="hello",
            from_uid="visitor-open-id",
            stream=False,
        ),
        request=SimpleNamespace(state=SimpleNamespace()), db=_NoOpDB(),
    )

    assert result["message"] == "ok"
    assert handle_ai_mock.await_args.kwargs["agent_id"] == str(platform_agent_id)
    assert handle_ai_mock.await_args.kwargs["knowledge_channel"] == "wecom_kf"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stream", "failure_mode"),
    [(False, "result"), (True, "event"), (True, "exception")],
)
async def test_chat_failure_never_returns_or_persists_upstream_error(
    monkeypatch, stream: bool, failure_mode: str,
) -> None:
    private_error = "provider response with private-customer-13800000000"
    project_id = uuid4()
    platform = SimpleNamespace(id=uuid4(), agent_id=uuid4(), type="wecom")
    visitor = SimpleNamespace(
        id=uuid4(), project_id=project_id, is_unassigned=True,
        ai_disabled=None, is_last_message_from_ai=False,
        is_last_message_from_visitor=True, last_client_msg_no=None,
        humanization_skill_name=None, humanization_skill_enabled=False,
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service, "validate_platform_and_project",
        lambda *_args: (platform, SimpleNamespace(id=project_id)),
    )
    monkeypatch.setattr(chat_endpoints, "get_or_create_visitor", AsyncMock(
        return_value=(visitor, False),
    ))
    monkeypatch.setattr(
        chat_endpoints.chat_service, "resolve_service_mode",
        lambda *_args: "auto",
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service, "send_user_message_to_wukongim",
        AsyncMock(),
    )
    monkeypatch.setattr(chat_endpoints, "transfer_to_staff", AsyncMock(
        return_value=SimpleNamespace(success=True, assigned_staff_id=uuid4()),
    ))
    monkeypatch.setattr(
        chat_endpoints.wukongim_client, "send_visitor_profile_updated",
        AsyncMock(),
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service, "is_ai_disabled", lambda *_args: False,
    )
    monkeypatch.setattr(
        chat_endpoints, "_resolve_platform_agent_kwargs",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(chat_endpoints, "claim_ai_interaction", lambda *_args, **_kwargs: (
        SimpleNamespace(
            run=SimpleNamespace(id=uuid4(), response_client_msg_no="ai_private"),
            is_duplicate=False,
        )
    ))
    finish = MagicMock()
    monkeypatch.setattr(chat_endpoints, "mark_ai_interaction_finished", finish)
    monkeypatch.setattr(
        chat_endpoints.chat_service, "handle_ai_response_non_stream",
        AsyncMock(return_value={"success": False, "error": private_error}),
    )

    async def failed_stream(**_kwargs):
        if failure_mode == "exception":
            raise RuntimeError(private_error)
        yield {"event_type": "workflow_failed", "data": {
            "error_message": private_error,
        }}

    monkeypatch.setattr(
        chat_endpoints.chat_service, "process_ai_stream_to_wukongim",
        failed_stream,
    )
    request = ChatCompletionRequest(
        api_key="fixture", message="商品咨询", from_uid="external-user",
        source_message_id="source-private-error", stream=stream,
    )
    if stream:
        result = await chat_endpoints.chat_completion(
            request, request=SimpleNamespace(state=SimpleNamespace()),
            db=_NoOpDB(),
        )
        chunks = [chunk async for chunk in result.body_iterator]
        assert chunks and all(private_error not in chunk for chunk in chunks)
        assert '"event_type": "workflow_failed"' in chunks[0]
        assert finish.call_args.kwargs["error_message"] == (
            "ai_stream_failed" if failure_mode == "exception"
            else "ai_workflow_failed"
        )
    else:
        with pytest.raises(HTTPException) as error:
            await chat_endpoints.chat_completion(
                request, request=SimpleNamespace(state=SimpleNamespace()),
                db=_NoOpDB(),
            )
        assert error.value.status_code == 500
        assert private_error not in str(error.value.detail)
        assert finish.call_args.kwargs["error_message"] == "ai_processing_failed"


@pytest.mark.asyncio
async def test_chat_completion_omits_agent_id_without_platform_override(
    monkeypatch,
) -> None:
    """Visitor chat should defer to the project default agent when no override exists."""

    project_id = uuid4()
    assigned_staff_id = uuid4()
    visitor = SimpleNamespace(
        project_id=project_id, humanization_skill_name=None, humanization_skill_enabled=False,
        id=uuid4(),
        is_unassigned=True,
        ai_disabled=None,
        is_last_message_from_ai=False,
        is_last_message_from_visitor=True,
        last_client_msg_no=None,
    )
    platform = SimpleNamespace(
        id=uuid4(),
        agent_id=None,
        ai_mode="auto",
        type="website",
    )
    project = SimpleNamespace(id=project_id, api_key="ak_project")
    handle_ai_mock = AsyncMock(return_value={"success": True, "content": "ok"})

    monkeypatch.setattr(
        chat_endpoints.chat_service,
        "validate_platform_and_project",
        lambda _api_key, _db: (platform, project),
    )
    monkeypatch.setattr(
        chat_endpoints,
        "get_or_create_visitor",
        AsyncMock(return_value=(visitor, False)),
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service,
        "send_user_message_to_wukongim",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        chat_endpoints,
        "transfer_to_staff",
        AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                assigned_staff_id=assigned_staff_id,
                message="assigned",
                queue_position=None,
            )
        ),
    )
    monkeypatch.setattr(
        chat_endpoints.wukongim_client,
        "send_visitor_profile_updated",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service,
        "is_ai_disabled",
        lambda _platform, _visitor: False,
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service,
        "handle_ai_response_non_stream",
        handle_ai_mock,
    )
    interaction_run = SimpleNamespace(
        id=uuid4(),
        response_client_msg_no="ai_test",
        status="running",
    )
    monkeypatch.setattr(
        chat_endpoints,
        "claim_ai_interaction",
        lambda *_args, **_kwargs: SimpleNamespace(
            run=interaction_run,
            is_duplicate=False,
        ),
    )
    monkeypatch.setattr(
        chat_endpoints,
        "mark_ai_interaction_finished",
        lambda *_args, **_kwargs: None,
    )

    result = await chat_endpoints.chat_completion(
        ChatCompletionRequest(
            api_key="pk_test",
            message="hello",
            from_uid="visitor-open-id",
            stream=False,
        ),
        request=SimpleNamespace(state=SimpleNamespace()), db=_NoOpDB(),
    )

    assert result["message"] == "ok"
    assert "agent_id" not in handle_ai_mock.await_args.kwargs
    assert handle_ai_mock.await_args.kwargs["knowledge_channel"] == "web"


@pytest.mark.asyncio
async def test_staff_agent_chat_routes_agent_only(monkeypatch) -> None:
    """Staff AI chat should spawn a background run with only an agent target."""

    project_id = uuid4()
    agent_id = uuid4()
    schedule_background_mock = MagicMock()

    monkeypatch.setattr(
        chat_endpoints.chat_service,
        "send_user_message_to_wukongim",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        chat_endpoints.chat_service,
        "schedule_background_ai_interaction",
        schedule_background_mock,
    )

    response = await chat_endpoints.staff_agent_chat(
        StaffAgentChatRequest(agent_id=agent_id, message="hello"),
        db=_NoOpDB(),
        current_user=SimpleNamespace(
            id=uuid4(),
            project_id=project_id,
            project=SimpleNamespace(id=project_id, api_key="ak_project"),
        ),
    )

    assert response.success is True
    assert schedule_background_mock.call_args.kwargs["agent_id"] == str(agent_id)
    assert schedule_background_mock.call_args.kwargs["knowledge_channel"] == "internal"
    assert "team_id" not in schedule_background_mock.call_args.kwargs


def test_chat_openapi_uses_agent_route_not_team_route(client) -> None:
    """OpenAPI should expose the single-agent route and drop the team-named route."""

    schema = client.get("/v1/openapi.json").json()

    assert "/v1/chat/agent" in schema["paths"]
    assert "/v1/chat/team" not in schema["paths"]


@pytest.mark.asyncio
async def test_create_platform_uses_single_agent_id(monkeypatch) -> None:
    """Platform creation should persist the new single-agent override field."""

    db = _PlatformDB()
    agent_id = uuid4()
    project_id = uuid4()
    lookup = AsyncMock(return_value={"id": str(agent_id)})
    monkeypatch.setattr(ai_client, "get_agent", lookup)

    monkeypatch.setattr(platform_endpoints, "generate_api_key", lambda: "pk_platform")
    monkeypatch.setattr(
        platform_endpoints,
        "_build_platform_response",
        lambda platform, language="zh": platform,
    )

    response = await platform_endpoints.create_platform(
        PlatformCreate(
            name="Website",
            type=PlatformType.WEBSITE,
            agent_id=agent_id,
        ),
        db=db,
        current_user=SimpleNamespace(project_id=project_id, username="alice"),
    )

    assert response.agent_id == agent_id
    assert db.added[0].agent_id == agent_id
    lookup.assert_awaited_once_with(
        project_id=str(project_id), agent_id=str(agent_id),
        include_tools=False, include_collections=False, include_workflows=False,
    )
