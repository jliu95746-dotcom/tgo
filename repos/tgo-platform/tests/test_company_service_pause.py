"""Paused service sends a system notice and never records an AI reply."""

import httpx
import pytest

from app.domain.entities import ChatCompletionRequest, StreamEvent
from app.domain.services import dispatcher
from app.infra.http import HttpxTgoApiClient
from tests.test_wecom_dispatcher_contract import FakeAdapter, FakeSession, FakeSSEManager, FakeTgoApiClient, make_message


@pytest.mark.asyncio
@pytest.mark.parametrize("code,paused", [("SUBSCRIPTION_EXPIRED", True), ("AI_QUOTA_EXHAUSTED", False)])
async def test_only_subscription_expiry_becomes_pause_notice(code, paused):
    client = HttpxTgoApiClient("https://synthetic.invalid")
    await client.aclose()
    client._client = httpx.AsyncClient(base_url="https://synthetic.invalid", transport=httpx.MockTransport(lambda _: httpx.Response(402, json={"error": {"code": code}})))
    request = ChatCompletionRequest(api_key="synthetic", message="hello", from_uid="synthetic")
    try:
        if paused:
            frames = [frame async for frame in client.chat_completion(request)]
            assert frames == [b'event: service_paused', b'data: {"event_type":"service_paused"}']
        else:
            with pytest.raises(httpx.HTTPStatusError):
                _ = [frame async for frame in client.chat_completion(request)]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_wecom_pause_notice_is_not_an_ai_receipt(monkeypatch):
    adapter = FakeAdapter()
    async def select_adapter(*args, **kwargs):
        return adapter
    monkeypatch.setattr(dispatcher, "select_adapter_for_target", select_adapter)
    result = await dispatcher.process_message(make_message(), FakeSession(), FakeTgoApiClient(), FakeSSEManager([StreamEvent(event="service_paused", payload={"event_type": "service_paused"})]))
    assert result is None
    assert adapter.final_payloads == [{"text": "当前客服服务已暂停，请稍后再试。"}]
