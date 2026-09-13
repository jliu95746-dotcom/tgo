"""Public Stop succeeds only after the matching private cleanup callback."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.internal.router import internal_router
from app.api.v1.endpoints.ai_runs import router as public_cancel_router
from app.core.security import get_current_active_user
from app.services import ai_reply_control as control
from app.services import chat_service

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("stage", ["lookup", "rewrite", "audit"])
async def test_public_stop_waits_for_private_end_receipt_without_execution_id(
    monkeypatch, stage
):
    project = uuid4()
    arguments = dict(
        project_id=str(project),
        user_id=str(uuid4()),
        message="绿色有吗？",
        channel_id=f"{uuid4()}-vtr",
        channel_type=251,
        client_msg_no=uuid4().hex,
        from_uid="fixture-agent",
    )
    private_app, public_app = FastAPI(), FastAPI()
    private_app.include_router(internal_router, prefix="/internal")
    public_app.include_router(public_cancel_router, prefix="/runs")
    public_app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        project_id=project
    )
    waiting, cleaned, release_receipt = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    phases = []
    forward = AsyncMock()
    stop = AsyncMock(return_value={"run_id": "unknown", "cancelled": False})
    monkeypatch.setattr(control, "POLL_SECONDS", 0.005)
    monkeypatch.setattr(chat_service, "forward_ai_event_to_wukongim", forward)
    monkeypatch.setattr(
        chat_service, "recent_customer_messages", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(chat_service.ai_client, "cancel_supervisor_run", stop)

    async def held_model(phase):
        waiting.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
            await release_receipt.wait()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=private_app),
                base_url="http://owned.test",
            ) as private:
                response = await private.post(
                    "/internal/ai/reply-phases/ended",
                    json={
                        **phase.model_dump(mode="json"),
                        "status": "ended",
                    },
                )
                assert response.status_code == 200

    async def factual(**kwargs):
        phases.append(kwargs["reply_phase"])
        if stage == "lookup":
            await held_model(kwargs["reply_phase"])
        yield "agent_response_complete", {
            "data": {"success": True, "final_content": "这款没有绿色。"}
        }
        yield "workflow_completed", {"data": {}}

    async def expression(**kwargs):
        phases.append(kwargs["reply_phase"])
        if stage == "audit" and len(phases) == 2:
            return {"content": "这款没有绿色。"}
        await held_model(kwargs["reply_phase"])

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", factual)
    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent", expression)

    async def consume():
        return [
            event
            async for event in chat_service.process_ai_stream_to_wukongim(**arguments)
        ]

    worker = asyncio.create_task(consume())
    cancel = None
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=public_app), base_url="http://owned.test"
        ) as public:
            cancel = asyncio.create_task(
                public.post(
                    "/runs/cancel",
                    json={
                        "client_msg_no": arguments["client_msg_no"],
                    },
                )
            )
            await asyncio.wait_for(cleaned.wait(), 1)
            assert not cancel.done()
            release_receipt.set()
            response = await asyncio.wait_for(cancel, 1)
        assert response.status_code == 202
        assert response.json()["status"] == "cancelled"
        await asyncio.wait_for(worker, 1)
        assert len({phase.phase_id for phase in phases}) == len(phases)
        stop.assert_not_awaited()
        assert not any(
            call.kwargs["event_type"] == "workflow_completed"
            for call in forward.await_args_list
        )
    finally:
        release_receipt.set()
        pending = [worker] + ([cancel] if cancel else [])
        for task in pending:
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
