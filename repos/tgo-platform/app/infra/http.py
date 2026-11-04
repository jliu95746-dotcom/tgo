from __future__ import annotations
import httpx
from typing import AsyncIterator

from app.domain.entities import ChatCompletionRequest
from app.domain.ports import TgoApiClient
from app.infra.visitor_client import channel_registration_headers


class HttpxTgoApiClient(TgoApiClient):
    durable_turns = True
    def __init__(self, base_url: str, timeout: float | None = None) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def chat_completion(self, req: ChatCompletionRequest) -> AsyncIterator[bytes]:
        url = "/v1/chat/completion"
        async with self._client.stream("POST", url, json=req.model_dump(), headers=channel_registration_headers()) as r:
            if r.status_code == 402:
                await r.aread()
                try:
                    payload = r.json()
                    error = payload.get("error") if isinstance(payload, dict) else None
                    paused = isinstance(error, dict) and error.get("code") == "SUBSCRIPTION_EXPIRED"
                except ValueError:
                    paused = False
                if paused:
                    yield b"event: service_paused"
                    yield b'data: {"event_type":"service_paused"}'
                    return
            r.raise_for_status()
            async for line in r.aiter_lines():
                if line:
                    yield line.encode()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ingest_turn(self, req: ChatCompletionRequest) -> dict[str, object]:
        response = await self._client.post("/v1/chat/turns/inbound", json=req.model_dump())
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("event_type") not in {
            "accepted", "received", "human_handoff", "ai_disabled", "assist_mode", "queued"}:
            raise ValueError("Invalid turn registration receipt")
        return body

    async def turn_action(self, turn_id: str, action: str, body: dict[str, object]) -> dict[str, object]:
        from uuid import UUID
        if action not in {"claim", "receipt"}:
            raise ValueError("Invalid turn delivery action")
        response = await self._client.post(f"/v1/chat/turns/{UUID(turn_id)}/{action}", json=body)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("Invalid turn delivery response")
        return value

