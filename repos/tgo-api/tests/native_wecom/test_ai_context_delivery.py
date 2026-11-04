from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.schemas.humanization import ConversationTurn
from app.services import chat_service
from app.services.knowledge_channel import resolve_platform_knowledge_channel


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_type", ["wecom", "website"])
async def test_actual_model_request_receives_native_staff_history(monkeypatch, platform_type):
    captured = {}
    history = [
        ConversationTurn(role="customer", content="我要黑色"),
        ConversationTurn(role="staff", content="已记录黑色，计划下周下单"),
    ]
    monkeypatch.setattr(chat_service, "recent_customer_messages", AsyncMock(return_value=history))
    monkeypatch.setattr(chat_service, "forward_ai_event_to_wukongim", AsyncMock())

    async def stream(**kwargs):
        captured.update(kwargs)
        if False:
            yield "agent_response_complete", {}

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", stream)
    channel = resolve_platform_knowledge_channel(platform_type).value
    visitor = str(uuid4())
    events = [event async for event in chat_service._process_ai_reply_to_wukongim(
        project_id=str(uuid4()), user_id=visitor,
        message="刚才人工客服回复了什么？", channel_id=visitor + "-vtr",
        channel_type=251, client_msg_no="native-context-probe", from_uid="ai-staff",
        knowledge_channel=channel,
    )]
    assert events
    assert captured["knowledge_channel"] == channel
    if platform_type == "wecom":
        assert '"role": "staff"' in captured["system_message"]
        assert "已记录黑色，计划下周下单" in captured["system_message"]
    else:
        assert '"role": "staff"' not in captured["system_message"]
