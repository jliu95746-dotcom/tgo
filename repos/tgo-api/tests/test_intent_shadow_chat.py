"""Assist-mode shadow analysis must never publish an AI customer reply."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse

from app.api.v1.endpoints import chat
from app.schemas.chat import ChatCompletionRequest


class _Query:
    def filter(self, *_criteria: object) -> _Query:
        return self

    def first(self) -> SimpleNamespace:
        return SimpleNamespace(staff_id=uuid4())


class _Session:
    def query(self, _model: type[object]) -> _Query:
        return _Query()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", (False, True))
@pytest.mark.parametrize("classification_fails", (False, True))
async def test_assist_shadow_never_starts_customer_ai_after_mode_changes(
    monkeypatch: pytest.MonkeyPatch, stream: bool, classification_fails: bool
) -> None:
    project = SimpleNamespace(id=uuid4())
    platform = SimpleNamespace(
        id=uuid4(), project_id=project.id, ai_mode="assist", type="website"
    )
    visitor = SimpleNamespace(
        id=uuid4(), is_unassigned=False, service_mode="assist", ai_disabled=True
    )
    calls: list[bool] = []

    class _ShadowOrchestrator:
        async def analyze_text_message(self, **kwargs: object) -> SimpleNamespace:
            calls.append(kwargs["shadow_only"] is True)
            # A concurrent setting change must not turn this shadow run into a reply.
            visitor.service_mode = "auto"
            visitor.ai_disabled = False
            if classification_fails:
                raise RuntimeError("classifier unavailable")
            return SimpleNamespace(routing_target="shadow_only", tool_context=None)

    async def resolve_visitor(
        *_args: object, **_kwargs: object
    ) -> tuple[object, bool, object]:
        return visitor, False, object()

    async def guarded(call: Callable[[], Awaitable[object]], _access: object) -> object:
        return await call()

    monkeypatch.setattr(
        chat.chat_service,
        "validate_platform_and_project",
        lambda *_args: (platform, project),
    )
    monkeypatch.setattr(chat, "resolve_chat_visitor", resolve_visitor)
    monkeypatch.setattr(chat, "guarded_chat_call", guarded)
    monkeypatch.setattr(chat, "check_chat_channel", lambda *_args: None)
    monkeypatch.setattr(
        chat, "MessageIntentOrchestrator", lambda _db: _ShadowOrchestrator()
    )

    request = Request(
        {"type": "http", "method": "POST", "path": "/v1/chat/completion", "headers": []}
    )
    response = await chat.chat_completion(
        ChatCompletionRequest(
            api_key="test-key",
            message="请帮我查询订单",
            from_uid="visitor-test",
            source_message_id="shadow-message-1",
            forward_user_message_to_wukongim=False,
            stream=stream,
        ),
        request,
        db=_Session(),  # type: ignore[arg-type]
        credentials=None,
        x_saas_service_token=None,
    )

    assert calls == [True]
    if stream:
        assert isinstance(response, StreamingResponse)
        payload = "".join(
            [
                chunk if isinstance(chunk, str) else chunk.decode("utf-8")
                async for chunk in response.body_iterator
            ]
        )
        assert "assist_mode" in payload
        assert "agent_content_chunk" not in payload
    else:
        assert isinstance(response, dict)
        assert response["event_type"] == "assist_mode"
