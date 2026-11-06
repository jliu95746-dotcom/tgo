"""The real chat transport and publication keep context credentials private."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.api.business_context_chat_route import BusinessContextChatRoute
from app.schemas.business_execution_context import BusinessRequestAuthorization
from app.schemas.chat import ChatCompletionRequest
from app.services.ai_client import AIServiceClient
from app.services.business_execution_transport import business_headers


def authorization():
    return BusinessRequestAuthorization(
        project_id=uuid4(),
        visitor_id=uuid4(),
        channel="web",
        request_id=uuid4(),
        context_digest="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
        context_token=SecretStr("private-context-token"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_real_ai_client_puts_secret_only_in_private_headers(monkeypatch, stream):
    auth = authorization()
    client = AIServiceClient()
    observed = []
    if not stream:
        transport = AsyncMock(
            return_value=httpx.Response(
                200, json={"success": True, "content": "result"}
            )
        )
        monkeypatch.setattr(client, "_make_request", transport)
        await client.run_supervisor_agent(
            "客户咨询",
            str(auth.project_id),
            user_id=str(auth.visitor_id),
            knowledge_channel="web",
            business_authorization=auth,
        )
        call = transport.await_args.kwargs
        observed.append((call["extra_headers"], call["json_data"]))
    else:
        original = httpx.AsyncClient

        def respond(request):
            observed.append((request.headers, json.loads(request.content)))
            return httpx.Response(
                200,
                content='event: workflow_completed\ndata: {"data":{"success":true}}\n\n',
                headers={"content-type": "text/event-stream"},
            )

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)),
        )
        events = [
            event
            async for event in client.run_supervisor_agent_stream(
                "客户咨询",
                str(auth.project_id),
                user_id=str(auth.visitor_id),
                knowledge_channel="web",
                business_authorization=auth,
            )
        ]
        assert events
    headers, body = observed[0]
    assert (
        headers["X-Business-Request-Context"] == auth.context_token.get_secret_value()
    )
    assert headers["X-Business-Request-ID"] == str(auth.request_id)
    assert auth.context_token.get_secret_value() not in json.dumps(body)
    assert (
        "context_token" not in auth.model_dump()
        and "private-context-token" not in repr(auth)
    )


@pytest.mark.parametrize("fault", ["project", "visitor", "channel", "expired"])
def test_wrong_customer_transport_scope_never_reaches_ai(fault):
    auth = authorization()
    project, visitor, channel = str(auth.project_id), str(auth.visitor_id), "web"
    if fault == "project":
        project = str(uuid4())
    elif fault == "visitor":
        visitor = str(uuid4())
    elif fault == "channel":
        channel = "wecom_kf"
    else:
        auth = auth.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
    with pytest.raises(ValueError):
        business_headers(auth, project, visitor, channel)


@pytest.mark.asyncio
async def test_chat_validation_cannot_echo_customer_session_on_invalid_input():
    app = FastAPI()
    router = __import__("fastapi").APIRouter(route_class=BusinessContextChatRoute)

    @router.post("/chat")
    def chat(body: ChatCompletionRequest):
        return body.model_dump()

    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        invalid = await client.post(
            "/chat",
            json={
                "message": "test",
                "from_uid": "visitor",
                "customer_sessions": [
                    {
                        "connection_id": "invalid",
                        "customer_session": "private-validation-session",
                    }
                ],
            },
        )
        assert (
            invalid.status_code == 422
            and "private-validation-session" not in invalid.text
        )
        valid = await client.post(
            "/chat",
            json={
                "api_key": "fixture-only",
                "message": "test",
                "from_uid": "visitor",
                "customer_sessions": [
                    {
                        "connection_id": str(uuid4()),
                        "customer_session": "private-validation-session",
                    }
                ],
            },
        )
        assert valid.status_code == 200 and "customer_sessions" not in valid.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_context_revoked_before_publication_reaches_neither_customer_consumer(
    monkeypatch, stream
):
    from app.services import chat_service

    auth = authorization()

    async def events(**kwargs):
        assert kwargs["business_authorization"] == auth
        yield (
            "agent_response_complete",
            {
                "data": {
                    "success": True,
                    "final_content": "must not publish",
                    "execution_reference": {},
                }
            },
        )

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)
    monkeypatch.setattr(chat_service, "ensure_customer_auto_reply", AsyncMock())
    monkeypatch.setattr(
        chat_service, "recent_customer_messages", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        chat_service, "rewrite_assist_draft", AsyncMock(return_value="must not publish")
    )
    monkeypatch.setattr(
        "app.services.agent_execution_publication.ensure_execution_can_publish",
        AsyncMock(),
    )
    monkeypatch.setattr(chat_service, "begin_reply_publication", AsyncMock())

    def revoke(*args):
        raise ValueError("customer context revoked")

    monkeypatch.setattr(chat_service, "verify_business_publication", revoke)
    forwarded = []
    monkeypatch.setattr(
        chat_service,
        "forward_ai_event_to_wukongim",
        AsyncMock(side_effect=lambda **kwargs: forwarded.append(kwargs)),
    )
    kwargs = dict(
        project_id=str(auth.project_id),
        message="客户咨询",
        channel_id="owned-channel",
        channel_type=251,
        client_msg_no="owned-attempt",
        from_uid="owned-staff",
        knowledge_channel="web",
        business_authorization=auth,
    )
    if stream:
        events = [
            event
            async for event in chat_service._process_ai_reply_to_wukongim(
                user_id=str(auth.visitor_id), **kwargs
            )
        ]
        assert not any(
            row["event_type"] in {"agent_content_chunk", "workflow_completed"}
            for row in events
        )
    else:

        async def source(**params):
            params.pop("visitor_chat_access", None)
            async for row in chat_service._process_ai_reply_to_wukongim(**params):
                yield row

        monkeypatch.setattr(chat_service, "process_ai_stream_to_wukongim", source)
        result = await chat_service.handle_ai_response_non_stream(
            visitor_id=str(auth.visitor_id), **kwargs
        )
        assert not result["success"]
    assert not any(
        row["event_type"] in {"agent_content_chunk", "workflow_completed"}
        for row in forwarded
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "stream", "background"])
async def test_visitor_ingress_issues_and_passes_context_to_each_existing_chat_path(
    monkeypatch, mode
):
    from app.api.v1.endpoints import chat as endpoint
    from tests.test_chat_agent_only_endpoints import _NoOpDB
    from tests.test_chat_agent_only_endpoints import isolate_visitor_transport
    isolate_visitor_transport(monkeypatch)

    auth = authorization()
    platform = SimpleNamespace(
        id=uuid4(), type="website", agent_id=None, ai_mode="auto"
    )
    project = SimpleNamespace(id=auth.project_id)
    visitor = SimpleNamespace(
        id=auth.visitor_id,
        project_id=auth.project_id,
        is_unassigned=True,
        ai_disabled=False,
        humanization_skill_name=None,
        humanization_skill_enabled=False,
        is_last_message_from_ai=False,
        is_last_message_from_visitor=True,
        last_client_msg_no=None,
    )
    monkeypatch.setattr(
        endpoint.chat_service,
        "validate_platform_and_project",
        lambda *_: (platform, project),
    )
    monkeypatch.setattr(
        endpoint, "get_or_create_visitor", AsyncMock(return_value=(visitor, False))
    )
    monkeypatch.setattr(
        endpoint,
        "transfer_to_staff",
        AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                assigned_staff_id=uuid4(),
                message="assigned",
                queue_position=None,
            )
        ),
    )
    monkeypatch.setattr(
        endpoint.wukongim_client, "send_visitor_profile_updated", AsyncMock()
    )
    monkeypatch.setattr(
        endpoint,
        "_resolve_platform_agent_kwargs",
        AsyncMock(return_value={"knowledge_channel": "web"}),
    )
    outcome = SimpleNamespace(
        routing_target="auto_reply", tool_context=None, excluded_tool_ids=(),
        contextual_reply=None,
    )
    classify = AsyncMock(return_value=outcome)
    monkeypatch.setattr(
        endpoint.MessageIntentOrchestrator, "analyze_text_message", classify
    )
    monkeypatch.setattr(
        endpoint,
        "claim_ai_interaction",
        lambda *_, **kwargs: SimpleNamespace(
            run=SimpleNamespace(
                id=auth.request_id, response_client_msg_no="original-attempt"
            ),
            is_duplicate=False,
        ),
    )
    monkeypatch.setattr(
        endpoint, "mark_ai_interaction_finished", lambda *_, **kwargs: None
    )
    issue = __import__("unittest.mock", fromlist=["Mock"]).Mock(return_value=auth)
    monkeypatch.setattr(
        endpoint,
        "BusinessExecutionContextService",
        lambda _: SimpleNamespace(issue=issue),
    )
    received = []

    async def sync(**kwargs):
        received.append(kwargs)
        return {"success": True, "content": "checked"}

    async def stream(**kwargs):
        received.append(kwargs)
        yield {"event_type": "workflow_completed", "data": {}}

    def background(**kwargs):
        received.append(kwargs)

    monkeypatch.setattr(endpoint.chat_service, "handle_ai_response_non_stream", sync)
    monkeypatch.setattr(endpoint.chat_service, "process_ai_stream_to_wukongim", stream)
    monkeypatch.setattr(
        endpoint.chat_service, "schedule_background_ai_interaction", background
    )
    body = ChatCompletionRequest(
        api_key="fixture-only",
        message="客户咨询",
        from_uid="owned-visitor",
        source_message_id="actual-message",
        forward_user_message_to_wukongim=False,
        stream=mode == "stream",
        wukongim_only=mode == "background",
        customer_sessions=[
            {
                "connection_id": str(uuid4()),
                "customer_session": "private-customer-session",
            }
        ],
    )
    response = await endpoint.chat_completion(body, request=SimpleNamespace(state=SimpleNamespace()), db=_NoOpDB())
    if mode == "stream":
        chunks = [chunk async for chunk in response.body_iterator]
        assert "private-customer-session" not in str(chunks)
    assert issue.call_args.args[0] == auth.request_id
    assert received[0]["business_authorization"] == auth
    assert received[0]["interaction_run_id"] == auth.request_id
    assert classify.await_args.kwargs["defer_business_tools"] is True
    assert "private-customer-session" not in (body.system_message or "")
