"""A device session belongs to a whole execution, never to one tool call."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.exceptions import ExternalServiceError
from app.services import device_execution as module


def context(**overrides):
    values = {
        "project_id": str(uuid4()),
        "agent": SimpleNamespace(
            id=uuid4(), name="测试员工", bound_device_id=str(uuid4())
        ),
        "disable_tools": False,
        "response_purpose": "standard",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def client(monkeypatch):
    fake = SimpleNamespace(
        start_session=AsyncMock(),
        heartbeat_session=AsyncMock(),
        finish_session=AsyncMock(),
    )
    monkeypatch.setattr(module, "device_control_client", fake)
    monkeypatch.setattr(module, "HEARTBEAT_SECONDS", 0.01)
    return fake


@pytest.mark.parametrize("success", [True, False])
async def test_record_covers_build_and_whole_execution(client, success):
    ctx = context()
    async with module.track_device_execution(ctx) as execution:
        assert execution is not None
        assert module.current_device_execution() is execution
        client.start_session.assert_awaited_once_with(execution.identity)
        client.finish_session.assert_not_awaited()
        execution.complete(success)
    assert execution.closed
    assert module.current_device_execution() is None
    client.finish_session.assert_awaited_once_with(
        execution.identity, "completed" if success else "failed"
    )


@pytest.mark.parametrize("skip", ["unbound", "disabled", "expression"])
async def test_non_device_execution_never_contacts_monitor(client, skip):
    ctx = context()
    if skip == "unbound":
        ctx.agent.bound_device_id = None
    elif skip == "disabled":
        ctx.disable_tools = True
    else:
        ctx.response_purpose = "expression"
    async with module.track_device_execution(ctx) as execution:
        assert execution is None
    client.start_session.assert_not_awaited()


async def test_start_failure_prevents_execution(client):
    client.start_session.side_effect = ExternalServiceError("device-control")
    with pytest.raises(ExternalServiceError):
        async with module.track_device_execution(context()):
            pytest.fail("Device work must not run without a session")
    assert module.current_device_execution() is None
    client.finish_session.assert_not_awaited()


async def test_build_failure_records_failure_and_keeps_original_error(client):
    with pytest.raises(ValueError, match="build failed"):
        async with module.track_device_execution(context()) as execution:
            raise ValueError("build failed")
    client.finish_session.assert_awaited_once_with(
        execution.identity, "failed"
    )
    assert execution.closed


async def test_external_cancellation_is_preserved_and_recorded(client):
    started = asyncio.Event()

    async def run():
        async with module.track_device_execution(context()):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(run())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.finish_session.await_args.args[1] == "cancelled"


async def test_heartbeat_keeps_concurrent_disconnect(client):
    async def run():
        async with module.track_device_execution(context()):
            await asyncio.Event().wait()

    task = asyncio.create_task(run())

    async def fail(*args):
        # The disconnect arrives while heartbeat's cancellation is queued.
        asyncio.get_running_loop().call_soon(task.cancel)
        raise RuntimeError("fixture disconnect")

    client.heartbeat_session.side_effect = fail
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert task.cancelling() == 1
    assert client.finish_session.await_args.args[1] == "cancelled"


@pytest.mark.parametrize("swallow_cancel", [False, True])
async def test_lost_heartbeat_stops_work_and_cannot_report_success(
    client, swallow_cancel
):
    client.heartbeat_session.side_effect = RuntimeError(
        "private upstream text"
    )

    async def run():
        async with module.track_device_execution(context()) as execution:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if not swallow_cancel:
                    raise
            execution.complete(True)

    with pytest.raises(ExternalServiceError, match="执行记录连接中断") as error:
        await asyncio.wait_for(run(), 2)
    assert "private upstream" not in str(error.value)
    assert client.finish_session.await_args.args[1] == "failed"


async def test_finish_failure_does_not_replace_real_result(client, caplog):
    client.finish_session.side_effect = RuntimeError("private credentials")
    async with module.track_device_execution(context()) as execution:
        execution.complete(True)
    assert execution.closed
    assert "private credentials" not in caplog.text


@pytest.mark.parametrize("same_device", [False, True])
async def test_parallel_sessions_keep_identity_and_headers_isolated(
    client, same_device
):
    from app.config import settings
    from jose import jwt

    contexts = [context(), context()]
    if same_device:
        contexts[1] = contexts[0]
    arrived = [asyncio.Event(), asyncio.Event()]

    async def run(index):
        ctx = contexts[index]
        async with module.track_device_execution(ctx) as execution:
            headers = module.device_headers_factory(
                ctx.project_id, ctx.agent.bound_device_id
            )
            arrived[index].set()
            await arrived[1 - index].wait()
            claims = jwt.decode(
                headers()["Authorization"][7:],
                settings.secret_key,
                algorithms=["HS256"],
                audience="tgo-device-control",
                issuer="tgo-internal",
            )
            assert claims["session_id"] == str(execution.identity.session_id)
            assert claims["project_id"] == ctx.project_id
            assert claims["device_id"] == ctx.agent.bound_device_id
            execution.complete(True)
        with pytest.raises(ExternalServiceError, match="已经结束"):
            headers()
        return claims["session_id"]

    ids = await asyncio.gather(run(0), run(1))
    assert len(set(ids)) == 2
    assert module.current_device_execution() is None


async def test_heartbeat_renews_until_execution_finishes(client):
    renewed = asyncio.Event()
    client.heartbeat_session.side_effect = lambda *args: renewed.set()
    async with module.track_device_execution(context()) as execution:
        await asyncio.wait_for(renewed.wait(), 2)
        client.heartbeat_session.assert_awaited_with(execution.identity)
        execution.complete(True)
    count = client.heartbeat_session.await_count
    await asyncio.sleep(0.03)
    assert client.heartbeat_session.await_count == count


async def test_nested_expression_does_not_inherit_scope(client):
    async with module.track_device_execution(context()) as outer:
        async with module.track_device_execution(
            context(response_purpose="expression")
        ) as inner:
            assert inner is None
            assert module.current_device_execution() is None
        assert module.current_device_execution() is outer
        outer.complete(True)
    client.start_session.assert_awaited_once()


async def test_captured_scope_rejects_different_device_and_project(client):
    ctx = context()
    async with module.track_device_execution(ctx):
        for project, device in [
            (str(uuid4()), ctx.agent.bound_device_id),
            (ctx.project_id, str(uuid4())),
        ]:
            with pytest.raises(ExternalServiceError, match="执行记录不匹配"):
                module.device_headers_factory(project, device)


def test_untracked_legacy_headers_have_no_session_claim():
    from jose import jwt

    headers = module.device_headers_factory(str(uuid4()), str(uuid4()))()
    assert "session_id" not in jwt.get_unverified_claims(
        headers["Authorization"][7:]
    )
