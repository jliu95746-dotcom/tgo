"""Cancellation must never close a replacement connection or a newer policy."""

from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.schemas.device_script_execution import ScriptReceiptQuery
from app.services import device_script_cancellation as cancellation
from app.services.device_script_publication import stored_script_policy
from test_script_publication import connection, row


def cancellation_fixture(monkeypatch):
    project, device = uuid4(), uuid4()
    conn = connection(project, device)
    conn.desired_script_policy = stored_script_policy(row(project, device))
    conn.script_session_id = "d" * 32
    current = [conn]
    monkeypatch.setattr(
        cancellation.tcp_connection_manager,
        "get_connection",
        lambda _: current[0],
    )
    close = AsyncMock()
    monkeypatch.setattr(
        cancellation.DeviceScriptPublicationService, "_close", close
    )
    query = ScriptReceiptQuery(
        protocol_version=1,
        session_id=conn.script_session_id,
        execution_id=str(uuid4()),
        policy_digest=conn.desired_script_policy.digest,
        parameters_digest="a" * 64,
        context_digest="b" * 64,
    )
    return conn, current, close, query


@pytest.mark.asyncio
@pytest.mark.parametrize("signaled", [False, True])
async def test_exact_ack_does_not_close_the_device(monkeypatch, signaled):
    conn, _, close, query = cancellation_fixture(monkeypatch)
    conn.send_request.return_value = {
        **query.model_dump(),
        "signaled": signaled,
    }
    await cancellation.cancel_script_execution(conn, query)
    close.assert_not_awaited()
    assert conn.send_request.call_args.args == (
        "scripts/cancel",
        query.model_dump(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", ["connection", "policy", "session", "none"]
)
async def test_bad_ack_only_closes_the_original_scope(monkeypatch, changed):
    conn, current, close, query = cancellation_fixture(monkeypatch)
    conn.send_request.return_value = {
        **query.model_dump(),
        "signaled": "false",
    }
    if changed == "connection":
        current[0] = connection(uuid4(), uuid4())
    elif changed == "policy":
        conn.desired_script_policy = stored_script_policy(
            row(uuid4(), uuid4(), 2)
        )
    elif changed == "session":
        conn.script_session_id = "e" * 32
    await cancellation.cancel_script_execution(conn, query)
    assert close.await_count == int(changed == "none")


@pytest.mark.asyncio
async def test_old_client_cannot_pass_cancellation_probe(monkeypatch):
    conn, _, close, _ = cancellation_fixture(monkeypatch)
    conn.send_request.return_value = None
    with pytest.raises(HTTPException) as error:
        await cancellation.require_script_cancellation(conn)
    assert error.value.status_code == 409
    close.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_matches_all_identity_fields(monkeypatch):
    conn, _, close, _ = cancellation_fixture(monkeypatch)

    async def reply(method, params, timeout):
        assert method == "scripts/cancel"
        return {**params, "signaled": False, "context_digest": "c" * 64}

    conn.send_request.side_effect = reply
    with pytest.raises(HTTPException):
        await cancellation.require_script_cancellation(conn)
    close.assert_not_awaited()
