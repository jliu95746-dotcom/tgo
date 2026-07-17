"""Tests for knowledge-channel propagation from customer platforms to tgo-ai."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.api.v1.endpoints.chat import _build_platform_agent_kwargs
from app.schemas.knowledge import KnowledgeChannel
from app.services.ai_client import AIServiceClient
import app.services.chat_service as chat_service


@pytest.mark.parametrize(
    ("platform_type", "expected_channel"),
    [
        ("wecom", KnowledgeChannel.WECOM_KF),
        ("website", KnowledgeChannel.WEB),
        ("phone", KnowledgeChannel.PHONE),
        ("whatsapp", KnowledgeChannel.APP),
    ],
)
def test_platform_runtime_kwargs_map_customer_channel(
    platform_type: str,
    expected_channel: KnowledgeChannel,
) -> None:
    agent_id = uuid4()
    platform = SimpleNamespace(type=platform_type, agent_id=agent_id)

    kwargs = _build_platform_agent_kwargs(platform)

    assert kwargs == {
        "agent_id": str(agent_id),
        "knowledge_channel": expected_channel.value,
    }


def test_platform_runtime_kwargs_keep_channel_without_explicit_agent() -> None:
    platform = SimpleNamespace(type="wecom", agent_id=None)

    assert _build_platform_agent_kwargs(platform) == {
        "knowledge_channel": KnowledgeChannel.WECOM_KF.value,
    }


@pytest.mark.asyncio
async def test_non_stream_ai_client_payload_includes_knowledge_channel() -> None:
    client = AIServiceClient()
    client._make_request = AsyncMock(return_value=object())
    client._handle_response = AsyncMock(return_value={"content": "ok"})

    await client.run_supervisor_agent(
        message="退款政策",
        project_id=str(uuid4()),
        knowledge_channel=KnowledgeChannel.WECOM_KF.value,
    )

    payload = client._make_request.await_args.kwargs["json_data"]
    assert payload["knowledge_channel"] == "wecom_kf"


@pytest.mark.asyncio
async def test_chat_service_forwards_channel_to_streaming_ai_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_stream(**kwargs):
        captured.update(kwargs)
        if False:
            yield "message", {}

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", fake_stream)

    events = [
        event
        async for event in chat_service.process_ai_stream_to_wukongim(
            project_id=str(uuid4()),
            user_id=str(uuid4()),
            message="退款政策",
            channel_id="channel-1",
            channel_type=1,
            client_msg_no="message-1",
            from_uid="agent-1-agent",
            knowledge_channel=KnowledgeChannel.WECOM_KF.value,
        )
    ]

    assert events == []
    assert captured["knowledge_channel"] == "wecom_kf"
