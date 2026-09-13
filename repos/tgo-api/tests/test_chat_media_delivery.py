"""Automatic media replies use the same checked publication boundary as text."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.schemas.chat_media import ChatMediaInput
from app.services import chat_service
from app.services.chat_media_analysis import PreparedChatMedia
from app.services.chat_media_service import MediaInputError


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("failure", [False, True])
async def test_media_is_recognized_before_reply_and_failure_never_publishes(
    monkeypatch, stream, failure
):
    media = ChatMediaInput(
        project_id=uuid4(),
        platform_id=uuid4(),
        visitor_id=uuid4(),
        message_type=2,
        reference="/v1/chat/files/owned",
        source_message_id="source",
    )
    prepare = AsyncMock(
        side_effect=MediaInputError("请补充文字说明。") if failure else None,
        return_value=PreparedChatMedia("识别到一只红色包", "仅确认客户需求", True),
    )
    monkeypatch.setattr(chat_service, "prepare_chat_media", prepare)
    calls = []

    async def events(**kwargs):
        calls.append(kwargs)
        yield (
            "agent_response_complete",
            {"data": {"success": True, "final_content": "是想问这款包吗？"}},
        )
        yield "workflow_completed", {"data": {"success": True}}

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)
    monkeypatch.setattr(
        chat_service, "rewrite_assist_draft", AsyncMock(return_value="是想问这款包吗？")
    )
    monkeypatch.setattr(
        chat_service, "recent_customer_messages", AsyncMock(return_value=[])
    )
    forward = AsyncMock()
    monkeypatch.setattr(chat_service, "forward_ai_event_to_wukongim", forward)
    kwargs = {
        "project_id": str(media.project_id),
        "message": media.reference,
        "channel_id": "channel",
        "channel_type": 251,
        "client_msg_no": "reply",
        "from_uid": "ai",
        "media_input": media,
    }
    if stream:
        output = [
            item
            async for item in chat_service.process_ai_stream_to_wukongim(
                user_id=str(media.visitor_id), **kwargs
            )
        ]
    else:
        output = await chat_service.handle_ai_response_non_stream(
            visitor_id=str(media.visitor_id), **kwargs
        )
    prepare.assert_awaited_once_with(media)
    published = [
        item
        for item in forward.await_args_list
        if item.kwargs["event_type"] == "workflow_completed"
    ]
    if failure:
        assert not calls and not published
        assert "请补充文字说明" in str(output)
    else:
        assert calls[0]["message"] == "识别到一只红色包"
        assert calls[0]["disable_tools"] is True
        assert "仅确认客户需求" in calls[0]["system_message"]
        assert len(published) == 1


@pytest.mark.asyncio
async def test_background_media_context_is_carried_until_processing(monkeypatch):
    media = ChatMediaInput(
        project_id=uuid4(),
        platform_id=uuid4(),
        visitor_id=uuid4(),
        message_type=4,
        reference="/v1/chat/files/owned",
        source_message_id="source",
    )
    run = AsyncMock()
    monkeypatch.setattr(chat_service, "run_background_ai_interaction", run)
    task = chat_service.schedule_background_ai_interaction(
        project_id=str(media.project_id),
        user_id=str(media.visitor_id),
        message=media.reference,
        channel_id="channel",
        channel_type=251,
        client_msg_no="reply",
        from_uid="ai",
        media_input=media,
    )
    await task
    assert run.await_args.kwargs["media_input"] is media
