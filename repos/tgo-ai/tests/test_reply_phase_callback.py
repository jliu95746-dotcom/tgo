"""Private phase receipts follow model cleanup and contain no customer content."""

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from starlette.responses import StreamingResponse

from app.schemas.reply_phase import ReplyPhaseIdentity
from app.services.api_service import APIServiceClient
from app.streaming.owned_response import own_execution

pytestmark = pytest.mark.asyncio


def identity():
    return ReplyPhaseIdentity(
        project_id=str(uuid4()),
        client_msg_no=uuid4().hex,
        generation=uuid4(),
        phase_id=uuid4(),
        proof=uuid4(),
    )


async def test_stream_receipt_waits_for_actual_execution_cleanup():
    cleanup, release, sent = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def execute():
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()

    async def body():
        yield "connected"
        await asyncio.Event().wait()

    task = asyncio.create_task(execute())

    async def receipt():
        assert task.done()

    notify = AsyncMock(side_effect=receipt)
    try:
        response = own_execution(StreamingResponse(body()), task, on_finished=notify)
    except BaseException:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise

    async def send(message):
        if message["type"] == "http.response.body":
            sent.set()

    async def receive():
        await sent.wait()
        return {"type": "http.disconnect"}

    request = asyncio.create_task(
        response(
            {"type": "http", "asgi": {"spec_version": "2.0"}},
            receive,
            send,
        )
    )
    try:
        await asyncio.wait_for(cleanup.wait(), 1)
        notify.assert_not_awaited()
        release.set()
        await asyncio.wait_for(request, 1)
        notify.assert_awaited_once()
    finally:
        release.set()
        for pending in (request, task):
            if not pending.done() and not pending.cancelling():
                pending.cancel()
        await asyncio.gather(request, task, return_exceptions=True)


@pytest.mark.parametrize("reply", ["matching", "wrong", "unavailable"])
async def test_callback_uses_private_configured_route_and_validates_receipt(reply):
    phase, requests = identity(), []

    async def respond(request):
        requests.append(request)
        if reply == "unavailable":
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "accepted": True,
                "phase_id": str(phase.phase_id if reply == "matching" else uuid4()),
            },
        )

    service = APIServiceClient()
    service.internal_api_url = "http://owned.test/internal"
    service._http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        assert await service.confirm_reply_phase_ended(phase) is (reply == "matching")
        assert 1 <= len(requests) <= 2
        assert all(
            request.url.path == "/internal/ai/reply-phases/ended"
            for request in requests
        )
        assert json.loads(requests[0].content) == {
            **phase.model_dump(mode="json"),
            "status": "ended",
        }
    finally:
        await service.aclose()
