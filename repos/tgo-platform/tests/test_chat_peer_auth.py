"""A public platform key cannot substitute for the channel service identity."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.domain.entities import ChatCompletionRequest
from app.infra.http import HttpxTgoApiClient


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [True, False])
async def test_channel_chat_proof_is_required_and_only_sent_in_its_header(
    monkeypatch, configured
):
    token = "owned-peer-private-fixture"
    monkeypatch.setattr(
        settings, "saas_internal_token", SecretStr(token) if configured else None
    )
    captured = []

    def respond(request):
        captured.append(request)
        return httpx.Response(200, text='data: {"event_type":"accepted"}\n\n')

    client = HttpxTgoApiClient("https://owned.invalid")
    await client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://owned.invalid", transport=httpx.MockTransport(respond)
    )
    request = ChatCompletionRequest(
        api_key="public-key", message="你好", from_uid="owned-platform-person"
    )
    try:
        if configured:
            result = [frame async for frame in client.chat_completion(request)]
            assert result == [b'data: {"event_type":"accepted"}']
            assert captured[0].headers["X-SaaS-Service-Token"] == token
            assert token not in str(captured[0].url)
            assert token not in json.dumps(json.loads(captured[0].content))
        else:
            with pytest.raises(RuntimeError, match="内部渠道服务凭据"):
                _ = [frame async for frame in client.chat_completion(request)]
            assert captured == []
    finally:
        await client.aclose()
