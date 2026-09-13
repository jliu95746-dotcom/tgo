"""Opt-in customer requests cannot leave model work after HTTP disconnect."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import StreamingResponse

from app.runtime.supervisor.application import service as runtime_module
from app.runtime.supervisor.application.service import SupervisorRuntimeService
from app.schemas.agent_run import SupervisorRunRequest
from app.schemas.reply_phase import ReplyPhaseIdentity
from app.streaming.owned_response import run_until_disconnect


pytestmark = pytest.mark.asyncio


def execution(monkeypatch, phase):
    runtime = SupervisorRuntimeService(
        session_factory=Mock(), tools_runtime_service=Mock(_settings=None)
    )
    state = SimpleNamespace(started=asyncio.Event(), stopped=asyncio.Event(), task=None)
    context = SimpleNamespace(
        agent=SimpleNamespace(id=uuid4(), name="隔离测试", bound_device_id=None),
        request_id="fixture",
        message="隔离测试",
        disable_tools=False,
        response_purpose="standard",
    )

    async def block():
        state.task = asyncio.current_task()
        state.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            # An awaited cleanup must finish before the response owner exits.
            await asyncio.sleep(0)
            state.stopped.set()

    async def prepare(*args):
        if phase == "prepare":
            await block()
        return context, str(context.agent.id)

    async def build(*args):
        if phase == "build":
            await block()
        return SimpleNamespace(agent=Mock())

    async def run(*args):
        await block()

    runtime._prepare_context = AsyncMock(side_effect=prepare)
    runtime._agent_builder.build_agent = AsyncMock(side_effect=build)
    runtime._agent_runner.run = AsyncMock(side_effect=run)
    runtime._agent_runner.stream = AsyncMock(side_effect=run)
    monkeypatch.setattr(runtime_module, "get_event_emitter", Mock(return_value=Mock()))
    monkeypatch.setattr(
        runtime_module, "create_workflow_events", Mock(return_value=Mock())
    )

    async def body():
        yield "event: connected\ndata: {}\n\n"
        await asyncio.Event().wait()

    monkeypatch.setattr(
        runtime_module, "create_sse_response", lambda *args: StreamingResponse(body())
    )
    return runtime, state


async def dispose(state):
    if state.task is not None:
        if not state.task.done() and not state.task.cancelling():
            state.task.cancel()
        await asyncio.gather(state.task, return_exceptions=True)


async def test_request_disconnect_ownership_is_explicit_and_off_by_default():
    assert SupervisorRunRequest(message="隔离测试").cancel_on_disconnect is False
    assert (
        SupervisorRunRequest(
            message="隔离测试", cancel_on_disconnect=True
        ).cancel_on_disconnect
        is True
    )


@pytest.mark.parametrize("phase", ["prepare", "build", "run"])
@pytest.mark.parametrize("owned", [False, True])
async def test_stream_disconnect_stops_only_owned_execution(monkeypatch, phase, owned):
    runtime, state = execution(monkeypatch, phase)
    payload = SupervisorRunRequest(message="隔离测试").model_copy(
        update={"cancel_on_disconnect": owned}
    )
    response = await runtime.stream(payload, uuid4(), http_request=Mock())
    sent = asyncio.Event()

    async def send(message):
        if message["type"] == "http.response.body":
            sent.set()

    async def receive():
        await sent.wait()
        return {"type": "http.disconnect"}

    try:
        await asyncio.wait_for(state.started.wait(), 1)
        await asyncio.wait_for(
            response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send),
            1,
        )
        assert state.stopped.is_set() is owned
        assert state.task.done() is owned
        if owned:
            assert not runtime._runs
    finally:
        await dispose(state)


@pytest.mark.parametrize("phase", ["prepare", "build", "run"])
async def test_sync_disconnect_awaits_owned_model_cleanup(monkeypatch, phase):
    runtime, state = execution(monkeypatch, phase)
    disconnected = asyncio.Event()
    request = SimpleNamespace(
        is_disconnected=AsyncMock(side_effect=disconnected.is_set)
    )
    payload = SupervisorRunRequest(message="隔离测试").model_copy(
        update={"cancel_on_disconnect": True}
    )
    task = asyncio.create_task(runtime.run(payload, uuid4(), http_request=request))
    try:
        await asyncio.wait_for(state.started.wait(), 1)
        disconnected.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert state.stopped.is_set() and state.task.done()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await dispose(state)


async def test_sync_opt_in_requires_request_instead_of_running_unowned(monkeypatch):
    runtime, _ = execution(monkeypatch, "prepare")
    payload = SupervisorRunRequest(message="隔离测试").model_copy(
        update={"cancel_on_disconnect": True}
    )
    with pytest.raises(ValueError, match="HTTP request"):
        await asyncio.wait_for(runtime.run(payload, uuid4()), 1)
    runtime._prepare_context.assert_not_awaited()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("phase", ["prepare", "run"])
async def test_actual_route_disconnect_owns_request_before_and_after_model_start(
    monkeypatch, stream, phase
):
    from app.api.v1.agents import router
    from app.dependencies import get_supervisor_runtime_service
    from app.streaming.event_emitter import get_event_emitter
    from app.streaming.sse_handler import create_sse_response

    runtime, state = execution(monkeypatch, phase)
    if stream:
        # Exercise real SSE headers, disconnect polling, and emitter cleanup.
        monkeypatch.setattr(runtime_module, "get_event_emitter", get_event_emitter)
        monkeypatch.setattr(runtime_module, "create_sse_response", create_sse_response)
    project = uuid4()
    reply_phase = ReplyPhaseIdentity(
        project_id=str(project),
        client_msg_no=uuid4().hex,
        generation=uuid4(),
        phase_id=uuid4(),
        proof=uuid4(),
    )

    async def report(identity):
        assert identity == reply_phase
        assert state.task.done() and state.stopped.is_set()
        assert not runtime._runs

    receipt = AsyncMock(side_effect=report)
    monkeypatch.setattr(runtime_module, "report_phase_ended", receipt)
    app = FastAPI()
    app.include_router(router, prefix="/agents")
    app.dependency_overrides[get_supervisor_runtime_service] = lambda: runtime
    incoming = asyncio.Queue()
    await incoming.put(
        {
            "type": "http.request",
            "more_body": False,
            "body": json.dumps(
                {
                    "message": "隔离测试",
                    "stream": stream,
                    "cancel_on_disconnect": True,
                    "reply_phase": reply_phase.model_dump(mode="json"),
                }
            ).encode(),
        }
    )
    scope = {
        "type": "http",
        "asgi": {"spec_version": "2.0"},
        "method": "POST",
        "http_version": "1.1",
        "scheme": "http",
        "path": "/agents/run",
        "raw_path": b"/agents/run",
        "query_string": f"project_id={project}".encode(),
        "headers": [(b"content-type", b"application/json")],
        "server": ("owned.test", 80),
        "client": ("owned.test", 1),
    }
    task = asyncio.create_task(app(scope, incoming.get, AsyncMock()))
    try:
        await asyncio.wait_for(state.started.wait(), 2)
        await incoming.put({"type": "http.disconnect"})
        if stream:
            await asyncio.wait_for(task, 2)
        else:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        assert state.task.done() and state.stopped.is_set()
        assert not runtime._runs
        receipt.assert_awaited_once_with(reply_phase)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await dispose(state)


async def test_sync_success_returns_result_and_removes_watcher():
    receive = AsyncMock(side_effect=asyncio.Queue().get)
    request = Request({"type": "http"}, receive)
    baseline = asyncio.all_tasks()
    result = await asyncio.wait_for(
        run_until_disconnect(AsyncMock(return_value="已完成"), request), 1
    )
    assert result == "已完成"
    assert asyncio.all_tasks() == baseline


async def test_already_disconnected_request_never_starts_operation():
    operation = AsyncMock()
    request = Request(
        {"type": "http"}, AsyncMock(return_value={"type": "http.disconnect"})
    )
    with pytest.raises(asyncio.CancelledError):
        await run_until_disconnect(operation, request)
    operation.assert_not_awaited()


async def test_owner_cancellation_waits_for_operation_cleanup():
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def operation():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    request = Request({"type": "http"}, asyncio.Queue().get)
    task = asyncio.create_task(run_until_disconnect(operation, request))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert cleaned.is_set()


def reply_identity(project):
    return ReplyPhaseIdentity(
        project_id=str(project),
        client_msg_no=uuid4().hex,
        generation=uuid4(),
        phase_id=uuid4(),
        proof=uuid4(),
    )


async def test_phase_requires_owned_request():
    with pytest.raises(ValidationError, match="requires cancel_on_disconnect"):
        SupervisorRunRequest(message="隔离测试", reply_phase=reply_identity(uuid4()))


@pytest.mark.parametrize("stream", [False, True])
async def test_phase_cannot_report_or_execute_for_another_project(monkeypatch, stream):
    runtime, _ = execution(monkeypatch, "prepare")
    receipt = AsyncMock()
    monkeypatch.setattr(runtime_module, "report_phase_ended", receipt)
    payload = SupervisorRunRequest(
        message="隔离测试",
        cancel_on_disconnect=True,
        reply_phase=reply_identity(uuid4()),
    )
    method = runtime.stream if stream else runtime.run
    with pytest.raises(ValueError, match="project does not match"):
        await method(payload, uuid4(), http_request=Mock())
    runtime._prepare_context.assert_not_awaited()
    receipt.assert_not_awaited()


async def test_normal_sync_reply_also_reports_after_task_cleanup(monkeypatch):
    runtime, _ = execution(monkeypatch, "prepare")
    project = uuid4()
    phase = reply_identity(project)
    result = runtime._build_failure_response("隔离结果")
    model_tasks = []

    async def run(*args):
        model_tasks.append(asyncio.current_task())
        return result

    async def report(identity):
        assert identity == phase
        assert model_tasks[0].done()

    runtime._run = AsyncMock(side_effect=run)
    receipt = AsyncMock(side_effect=report)
    monkeypatch.setattr(runtime_module, "report_phase_ended", receipt)
    payload = SupervisorRunRequest(
        message="隔离测试",
        cancel_on_disconnect=True,
        reply_phase=phase,
    )
    request = Request({"type": "http"}, asyncio.Queue().get)
    assert await runtime.run(payload, project, http_request=request) is result
    receipt.assert_awaited_once_with(phase)


async def test_stream_setup_failure_does_not_leave_work_before_receipt(monkeypatch):
    runtime, _ = execution(monkeypatch, "prepare")
    baseline = asyncio.all_tasks()
    project = uuid4()
    phase = reply_identity(project)

    async def report(identity):
        assert identity == phase
        assert asyncio.all_tasks() == baseline

    receipt = AsyncMock(side_effect=report)
    monkeypatch.setattr(runtime_module, "report_phase_ended", receipt)
    monkeypatch.setattr(
        runtime_module, "create_sse_response", Mock(side_effect=RuntimeError("fixture"))
    )
    payload = SupervisorRunRequest(
        message="隔离测试",
        cancel_on_disconnect=True,
        reply_phase=phase,
    )
    with pytest.raises(RuntimeError, match="fixture"):
        await runtime.stream(payload, project, http_request=Mock())
    runtime._prepare_context.assert_not_awaited()
    receipt.assert_awaited_once_with(phase)
