"""Retired channels cannot dispatch even before their mirrored rows sync."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.domain.services.dispatcher import select_adapter_for_target, process_message
from app.api.v1.internal import vision_agent_inbound, VisionAgentInboundMessage
from app.api.v1.messages import send_message, SendMessageRequest


@pytest.mark.asyncio
async def test_retired_channel_has_no_adapter():
    with pytest.raises(ValueError, match="retired"):
        await select_adapter_for_target(None, SimpleNamespace(type="wechat_personal"))


@pytest.mark.asyncio
async def test_retired_channel_never_calls_ai():
    db = AsyncMock()
    db.scalar.return_value = SimpleNamespace(type="wechat_personal")
    ai = AsyncMock()
    msg = SimpleNamespace(platform_id=uuid4(), platform_type="wechat_personal", platform_api_key="test-only")
    with pytest.raises(ValueError, match="retired"):
        await process_message(msg, db, ai, AsyncMock())
    assert not ai.mock_calls


@pytest.mark.asyncio
async def test_retired_inbound_is_rejected_before_processing():
    db = AsyncMock()
    db.scalar.return_value = SimpleNamespace(type="wechat_personal")
    msg = VisionAgentInboundMessage(platform_id=str(uuid4()), platform_type="wechat_personal", from_uid="test", content="test")
    with pytest.raises(HTTPException) as error:
        await vision_agent_inbound(msg, None, db)
    assert error.value.status_code == 410


@pytest.mark.asyncio
async def test_retired_staff_send_does_not_forward():
    db = AsyncMock()
    db.scalar.return_value = SimpleNamespace(type="wechat_personal")
    request = SimpleNamespace(state=SimpleNamespace(request_id="removal-test"))
    body = SendMessageRequest(platform_api_key="test-only", from_uid="staff-test", channel_id="test-vtr", channel_type=251, payload={"type": 1, "content": "test"})
    response = await send_message(body, request, db)
    assert response.status_code == 410
