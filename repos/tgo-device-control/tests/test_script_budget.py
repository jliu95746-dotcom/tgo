"""Budget observations cannot grant execution or expose receipt contents."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.device_script_budget import ScriptBudgetRead, ScriptBudgetWire
from app.schemas.device_script_policy import ScriptPolicyAck
from app.services import device_script_budget as module
from app.services.device_script_publication import stored_script_policy
from test_script_policy_contract import descriptor
from test_script_publication import connection, database, row


def fixture(monkeypatch, *, server_used=0, local_used=0):
    project, device = uuid4(), uuid4()
    saved = row(project, device)
    conn = connection(project, device)
    policy = stored_script_policy(saved)
    conn.script_policy_required = True
    conn.script_session_id = "d" * 32
    conn.desired_script_policy = policy
    conn.applied_script_policy_ack = ScriptPolicyAck(
        protocol_version=1,
        session_id="d" * 32,
        revision=policy.revision,
        digest=policy.digest,
    )
    current = [conn]
    monkeypatch.setattr(
        module.tcp_connection_manager, "get_connection", lambda _: current[0]
    )
    wire = dict(
        protocol_version=1,
        session_id="d" * 32,
        policy_digest=policy.digest,
        grant_digest=policy.grant_digest,
        policy_max_runs=policy.max_runs,
        policy_used=local_used,
        grant_max_runs=2,
        grant_used=local_used,
    )

    async def reply(method, params, timeout):
        if method == "scripts/list":
            assert params == {}
            return dict(
                protocol_version=1, scripts=[descriptor().model_dump()]
            )
        assert method == "scripts/budget/get"
        assert params == dict(
            protocol_version=1,
            session_id="d" * 32,
            policy_digest=policy.digest,
        )
        return wire

    conn.send_request = AsyncMock(side_effect=reply)
    db = database(
        device,
        saved,
        None,
        None,
        device,
        saved,
        None,
        None,
        server_used,
        server_used,
    )
    return project, device, saved, conn, current, wire, db


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "server,local,remaining", [(0, 0, 1), (1, 0, 0), (0, 1, 0)]
)
async def test_budget_uses_both_reservations_without_running(
    monkeypatch, server, local, remaining
):
    project, device, _, conn, _, _, db = fixture(
        monkeypatch, server_used=server, local_used=local
    )
    budget = await module.DeviceScriptBudgetService(db).get(project, device)
    assert budget.remaining_runs == remaining
    assert budget.can_enable is False
    assert budget.server_policy_used == server
    assert budget.device_policy_used == local
    assert budget.grant_max_runs == 2
    assert budget.project_id == project and budget.device_id == device
    assert [call.args[0] for call in conn.send_request.await_args_list] == [
        "scripts/list",
        "scripts/budget/get",
    ]
    db.add.assert_not_called()
    assert (
        "fields" not in budget.model_dump()
        and "parameters" not in budget.model_dump()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "project",
        "device",
        "missing",
        "revoked",
        "pending",
        "file_mode",
        "grant",
        "session",
        "digest",
        "limit",
        "extra",
        "malformed",
        "reconnect",
        "owner_changed",
        "policy_changed",
        "late_disconnect",
        "late_ack_loss",
    ],
)
async def test_budget_rejects_stale_or_unowned_observations(monkeypatch, case):
    project, device, saved, conn, current, wire, db = fixture(monkeypatch)
    if case == "project":
        conn.project_id = str(uuid4())
    elif case == "device":
        conn.device_db_id = str(uuid4())
    elif case == "missing":
        current[0] = None
    elif case == "revoked":
        saved.enabled = False
    elif case == "pending":
        conn.applied_script_policy_ack = None
    elif case == "file_mode":
        conn.desired_read_policy_digest = "a" * 64
    elif case == "grant":
        wire["grant_digest"] = "b" * 64
    elif case == "session":
        wire["session_id"] = "a" * 32
    elif case == "digest":
        wire["policy_digest"] = "b" * 64
    elif case == "limit":
        wire["grant_max_runs"] = 3
    elif case == "extra":
        wire["private_output"] = "private-marker"
    elif case == "malformed":
        wire["policy_used"] = True
    elif case.startswith("late_"):
        values = iter([
            device, saved, None, None, device, saved, None, None, 0, 0
        ])
        calls = 0

        async def scalar(_):
            nonlocal calls
            calls += 1
            value = next(values)
            if calls == 10:
                if case == "late_disconnect":
                    current[0] = None
                else:
                    conn.applied_script_policy_ack = None
            return value

        db.scalar.side_effect = scalar
    else:

        async def changed(method, params, timeout):
            if method == "scripts/list":
                return dict(
                    protocol_version=1, scripts=[descriptor().model_dump()]
                )
            if case == "reconnect":
                current[0] = connection(project, device)
            elif case == "owner_changed":
                db.scalar.side_effect = [None]
            else:
                saved.enabled = False
            return wire

        conn.send_request.side_effect = changed
    with pytest.raises(HTTPException) as error:
        await module.DeviceScriptBudgetService(db).get(project, device)
    assert error.value.status_code in {404, 409, 502}
    assert "private-marker" not in str(error.value)
    db.add.assert_not_called()


def test_budget_wire_rejects_inconsistent_counts_and_coercion():
    value = dict(
        protocol_version=1,
        session_id="a" * 32,
        policy_digest="b" * 64,
        grant_digest="c" * 64,
        policy_max_runs=1,
        policy_used=0,
        grant_max_runs=2,
        grant_used=1,
    )
    ScriptBudgetWire.model_validate(value)
    for change in [
        dict(policy_used=-1),
        dict(grant_used=3),
        dict(policy_used=True),
        dict(grant_max_runs="2"),
        dict(policy_used=1, grant_used=0),
    ]:
        with pytest.raises(ValidationError):
            ScriptBudgetWire.model_validate({**value, **change})
    assert ScriptBudgetRead.model_fields["can_enable"].default is False
