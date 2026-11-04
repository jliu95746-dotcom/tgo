"""Script policies are versioned configuration without execution permission."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.device_script_policy import DeviceScriptPolicy
from app.schemas.device_script_publication import (
    ScriptPolicyPublish,
    ScriptPolicyRevoke,
)
from app.services import device_script_publication as publication
from app.services.device_script_publication import (
    DeviceScriptPublicationService,
)
from app.services.tcp_connection_manager import TcpDeviceConnection
from test_script_policy_contract import descriptor


def connection(project, device):
    writer = MagicMock()
    writer.is_closing.return_value = False
    conn = TcpDeviceConnection(
        agent_id=str(device),
        project_id=str(project),
        device_db_id=str(device),
        name="owned-script-policy",
        version="test",
        capabilities=[],
        reader=MagicMock(),
        writer=writer,
        connection_instance_id="c" * 32,
    )
    conn.send_request = AsyncMock()
    return conn


def request(**changes):
    values = dict(
        expected_policy_revision=0,
        connection_instance_id="c" * 32,
        session_id="d" * 32,
        script_id="daily_report",
        grant_digest="a" * 64,
        timeout_seconds=10,
        max_output_bytes=4096,
        max_runs=1,
    )
    return ScriptPolicyPublish.model_validate({**values, **changes})


def row(project, device, revision=1):
    values = request().model_dump()
    values = {
        name: values[name]
        for name in (
            "script_id",
            "grant_digest",
            "timeout_seconds",
            "max_output_bytes",
            "max_runs",
        )
    }
    policy = publication.make_script_policy(revision, **values)
    return DeviceScriptPolicy(
        device_id=device,
        project_id=project,
        enabled=True,
        **policy.model_dump(exclude={"protocol_version"}),
        updated_at=datetime.now(timezone.utc),
    )


def database(*values):
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=values)
    db.commit, db.rollback, db.refresh = AsyncMock(), AsyncMock(), AsyncMock()
    return db


@pytest.mark.parametrize(
    "changes",
    [
        {"session_id": ""},
        {"connection_instance_id": "c" * 32 + "\n"},
        {"max_runs": True},
        {"max_runs": 101},
        {"script_id": "report\n"},
        {"expected_policy_revision": -1},
        {"command": "anything"},
        {"verified": True},
        {"script_path": "private.py"},
    ],
)
def test_publication_rejects_execution_fields_and_coercion(changes):
    with pytest.raises(ValidationError):
        request(**changes)


@pytest.mark.asyncio
async def test_foreign_device_cannot_query_publish_or_revoke():
    service = DeviceScriptPublicationService(database(None, None, None))
    for operation in (
        service.get(uuid4(), uuid4()),
        service.publish(uuid4(), uuid4(), request()),
        service.revoke(
            uuid4(), uuid4(), ScriptPolicyRevoke(expected_policy_revision=1)
        ),
    ):
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_stale_revision_does_not_send_configuration(monkeypatch):
    project, device = uuid4(), uuid4()
    saved = row(project, device, revision=2)
    db = database(device, saved)
    conn = connection(project, device)
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )
    with pytest.raises(HTTPException) as error:
        await DeviceScriptPublicationService(db).publish(
            project, device, request()
        )
    assert error.value.status_code == 409
    db.commit.assert_not_awaited()
    conn.send_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["file", "screen"])
async def test_another_active_mode_blocks_script_publication(conflict):
    device = uuid4()
    db = database(
        device,
        None,
        device if conflict == "file" else None,
        device if conflict == "screen" else None,
    )
    with pytest.raises(HTTPException) as error:
        await DeviceScriptPublicationService(db).publish(
            uuid4(), device, request()
        )
    assert error.value.status_code == 409
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_revocation_keeps_history_and_closes_current_script_mode(
    monkeypatch,
):
    project, device = uuid4(), uuid4()
    saved = row(project, device)
    db = database(device, saved)
    conn = connection(project, device)
    conn.script_policy_required = True
    conn.desired_script_policy = publication.stored_script_policy(saved)
    conn.applied_script_policy_ack = None
    conn.script_session_id = "d" * 32
    close = AsyncMock()
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )
    monkeypatch.setattr(
        publication.tcp_connection_manager, "unregister_connection", close
    )
    result = await DeviceScriptPublicationService(db).revoke(
        project, device, ScriptPolicyRevoke(expected_policy_revision=1)
    )
    assert result.status == "revoked" and result.policy.revision == 2
    assert result.verified is False and saved.enabled is False
    assert saved.revoked_at is not None
    close.assert_awaited_once_with(str(device), expected_connection=conn)


def test_policy_limits_are_bounded_by_current_local_review():
    item = descriptor()
    scope = publication.make_script_policy(
        1,
        script_id=item.script_id,
        grant_digest=item.grant_digest,
        timeout_seconds=item.timeout_seconds,
        max_output_bytes=item.max_output_bytes,
        max_runs=item.max_runs,
    )
    assert publication.validate_script_scope(item, scope)
    assert not publication.validate_script_scope(
        item, scope.model_copy(update={"max_runs": 3})
    )
    assert not publication.validate_script_scope(
        item.model_copy(update={"effect": "write"}), scope
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ack_fault", [None, "session_id", "digest", "revision"]
)
async def test_publication_persists_before_rpc_and_only_exact_ack_is_active(
    monkeypatch, ack_fault
):
    project, device = uuid4(), uuid4()
    saved = row(project, device)
    db = database(device, None, None, None, device, saved, None, None)
    conn = connection(project, device)
    active = False

    async def reply(method, params, timeout):
        nonlocal active
        if method == "scripts/status":
            return dict(
                protocol_version=1,
                state="active" if active else "awaiting_policy",
                session_id="d" * 32,
                reason="policy_confirmed" if active else "policy_required",
            )
        if method == "scripts/list":
            return dict(
                protocol_version=1, scripts=[descriptor().model_dump()]
            )
        assert method == "scripts/policy/apply"
        assert db.commit.await_count == 1
        active = True
        ack = dict(
            protocol_version=1,
            session_id=params["session_id"],
            revision=params["policy"]["revision"],
            digest=params["policy"]["digest"],
        )
        if ack_fault:
            ack[ack_fault] = (
                7
                if ack_fault == "revision"
                else "e" * (32 if ack_fault == "session_id" else 64)
            )
        return ack

    conn.send_request.side_effect = reply
    close = AsyncMock()
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )
    monkeypatch.setattr(
        publication.tcp_connection_manager, "unregister_connection", close
    )
    result = await DeviceScriptPublicationService(db).publish(
        project, device, request()
    )
    assert result.verified is False and saved.enabled
    assert result.status == ("active" if ack_fault is None else "pending")
    assert db.commit.await_count == 2
    if ack_fault:
        close.assert_awaited_once_with(str(device), expected_connection=conn)
    else:
        close.assert_not_awaited()


@pytest.mark.asyncio
async def test_persisted_policy_is_not_sent_after_reacquire_failure(
    monkeypatch,
):
    project, device = uuid4(), uuid4()
    db = database(
        device, None, None, None, RuntimeError("owned storage failure")
    )
    conn = connection(project, device)
    conn.send_request.side_effect = [
        dict(
            protocol_version=1,
            state="awaiting_policy",
            session_id="d" * 32,
            reason="policy_required",
        ),
        dict(protocol_version=1, scripts=[descriptor().model_dump()]),
    ]
    close = AsyncMock()
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )
    monkeypatch.setattr(
        publication.tcp_connection_manager, "unregister_connection", close
    )
    with pytest.raises(RuntimeError, match="owned storage failure"):
        await DeviceScriptPublicationService(db).publish(
            project, device, request()
        )
    assert db.commit.await_count == 1
    assert all(
        call.args[0] != "scripts/policy/apply"
        for call in conn.send_request.await_args_list
    )
    close.assert_awaited_once_with(str(device), expected_connection=conn)


@pytest.mark.asyncio
async def test_changed_local_review_invalidates_an_existing_confirmation(
    monkeypatch,
):
    from app.schemas.device_script_policy import ScriptPolicyAck

    project, device = uuid4(), uuid4()
    saved = row(project, device)
    conn = connection(project, device)
    conn.script_policy_required = True
    conn.desired_script_policy = publication.stored_script_policy(saved)
    conn.script_session_id = "d" * 32
    conn.applied_script_policy_ack = ScriptPolicyAck(
        protocol_version=1,
        session_id="d" * 32,
        revision=saved.revision,
        digest=saved.digest,
    )
    conn.send_request.side_effect = [
        dict(
            protocol_version=1,
            state="active",
            session_id="d" * 32,
            reason="policy_confirmed",
        ),
        {"error": {"code": -32003, "message": "reviewed file changed"}},
    ]
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )
    result = await DeviceScriptPublicationService(database(device, saved)).get(
        project, device
    )
    assert (
        result.status == "pending" and conn.applied_script_policy_ack is None
    )
