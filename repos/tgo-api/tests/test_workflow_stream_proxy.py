"""The relay preserves admission errors and closes a disconnected upstream."""

from types import SimpleNamespace
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.v1.endpoints.ai_workflows import router, workflow_client
from app.core.security import get_current_active_user
from app.services.workflow_stream import prepare_workflow_stream
from app.services import workflow_stream


def client_app():
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    actor = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin", token_version=3)
    app.state.actor = actor
    app.dependency_overrides[get_current_active_user] = lambda: actor
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 404, 409, 422, 503])
async def test_upstream_admission_error_is_not_a_200_broken_stream(monkeypatch, status):
    closed = []

    async def stream():
        try:
            raise HTTPException(status, detail={"message": "流程未获准执行", "errors": []})
            yield "unreachable"
        finally:
            closed.append(True)

    monkeypatch.setattr(workflow_client, "execute_workflow", AsyncMock(return_value=stream()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client_app()), base_url="http://test") as client:
        response = await client.post("/v1/ai/workflows/owned-flow/execute", json={"stream": True, "inputs": {}})
    assert response.status_code == status
    assert response.json()["detail"]["message"] == "流程未获准执行"
    assert closed == [True]


@pytest.mark.asyncio
async def test_caller_stop_closes_prefetched_upstream():
    closed = []

    async def stream():
        try:
            yield "first"
            yield "second"
        finally:
            closed.append(True)

    prepared = await prepare_workflow_stream(stream())
    assert await anext(prepared) == "first"
    await prepared.aclose()
    assert closed == [True]


@pytest.mark.asyncio
async def test_empty_upstream_is_a_502_admission_failure():
    async def empty():
        if False:
            yield "unreachable"

    with pytest.raises(HTTPException) as error:
        await prepare_workflow_stream(empty())
    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_admission_wait_is_bounded_and_cleans_up(monkeypatch):
    closed = []

    async def slow():
        try:
            await asyncio.Event().wait()
            yield "never"
        finally:
            closed.append(True)

    monkeypatch.setattr(workflow_stream, "ADMISSION_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(HTTPException) as error:
        await prepare_workflow_stream(slow())
    assert error.value.status_code == 504
    assert closed == [True]


@pytest.mark.asyncio
async def test_cancel_during_admission_closes_upstream():
    entered = asyncio.Event()
    closed = []

    async def slow():
        try:
            entered.set()
            await asyncio.Event().wait()
            yield "never"
        finally:
            closed.append(True)

    task = asyncio.create_task(prepare_workflow_stream(slow()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]


@pytest.mark.asyncio
async def test_successful_stream_preserves_all_frames_and_closes_upstream(monkeypatch):
    frames = ['data: {"event":"workflow_started"}\n\n', 'data: {"event":"workflow_finished"}\n\n']
    closed = []

    async def stream():
        try:
            for frame in frames:
                yield frame
        finally:
            closed.append(True)

    monkeypatch.setattr(workflow_client, "execute_workflow", AsyncMock(return_value=stream()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client_app()), base_url="http://test") as client:
        response = await client.post("/v1/ai/workflows/owned-flow/execute", json={"stream": True})
    assert response.status_code == 200
    assert response.text == "".join(frames)
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync", "async", "stream"])
async def test_execution_identity_comes_from_authenticated_staff(monkeypatch, mode):
    application = client_app()
    stamp = "2026-11-05T00:00:00Z"
    if mode == "stream":
        async def frames():
            yield 'data: {"event":"workflow_started"}\n\n'
        receipt = frames()
    elif mode == "async":
        receipt = {"id": str(uuid4()), "workflow_id": "owned-flow", "status": "pending", "started_at": stamp}
    else:
        receipt = {"success": True, "output": "answer", "metadata": {"duration": 0.1, "startTime": stamp, "endTime": stamp}}
    dispatch = AsyncMock(return_value=receipt)
    monkeypatch.setattr(workflow_client, "execute_workflow", dispatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.post("/v1/ai/workflows/owned-flow/execute", json={
            "stream": mode == "stream", "async": mode == "async",
            "staff_id": str(uuid4()), "staff_token_version": 999,
            "owner": {"unmetered": True},
        })
    assert response.status_code == 200
    assert dispatch.await_args.kwargs == {
        "staff_id": application.state.actor.id,
        "staff_token_version": application.state.actor.token_version,
    }
    assert "owner" not in dispatch.await_args.args[2]
