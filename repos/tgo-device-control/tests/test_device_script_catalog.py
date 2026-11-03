"""Discovery checks current ownership and connection without execution."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import device_script_catalog as module


def fixture(monkeypatch):
    project, device = uuid4(), uuid4()
    response = {"protocol_version": 1, "scripts": []}
    connection = SimpleNamespace(
        agent_id=str(device),
        device_db_id=str(device),
        project_id=str(project),
        connection_instance_id="owned-connection",
        writer=SimpleNamespace(is_closing=lambda: False),
        send_request=AsyncMock(return_value=response),
    )
    current = [connection]
    monkeypatch.setattr(
        module.tcp_connection_manager, "get_connection", lambda _: current[0]
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = device
    return project, device, connection, current, db


@pytest.mark.asyncio
async def test_catalog_checks_ownership_and_does_not_execute(monkeypatch):
    project, device, connection, _, db = fixture(monkeypatch)
    result = await module.DeviceScriptCatalogService(db).get_catalog(
        project, device
    )
    assert result.protocol_version == 1 and result.scripts == []
    connection.send_request.assert_awaited_once_with(
        "scripts/list", {}, timeout=10
    )
    assert db.scalar.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["missing", "offline", "foreign", "wrong_device", "closed"]
)
async def test_inaccessible_catalog_does_not_contact_a_device(
    monkeypatch, failure
):
    project, device, connection, current, db = fixture(monkeypatch)
    expected = 404 if failure == "missing" else 409
    if failure == "missing":
        db.scalar.return_value = None
    elif failure == "offline":
        current[0] = None
    elif failure == "foreign":
        connection.project_id = str(uuid4())
    elif failure == "closed":
        connection.writer.is_closing = lambda: True
    else:
        connection.device_db_id = str(uuid4())
    with pytest.raises(HTTPException) as error:
        await module.DeviceScriptCatalogService(db).get_catalog(
            project, device
        )
    assert error.value.status_code == expected
    connection.send_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["reconnect", "changed_owner", "removed", "instance"]
)
async def test_delayed_catalog_is_not_accepted_after_scope_changes(
    monkeypatch, failure
):
    project, device, connection, current, db = fixture(monkeypatch)

    async def respond(*_args, **_kwargs):
        if failure == "reconnect":
            current[0] = SimpleNamespace(**vars(connection))
        elif failure == "changed_owner":
            connection.project_id = str(uuid4())
        elif failure == "instance":
            connection.connection_instance_id = "replacement"
        else:
            db.scalar.return_value = None
        return {"protocol_version": 1, "scripts": []}

    connection.send_request.side_effect = respond
    with pytest.raises(HTTPException) as error:
        await module.DeviceScriptCatalogService(db).get_catalog(
            project, device
        )
    assert error.value.status_code in {404, 409}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        None,
        {"error": {"code": -32601, "message": "private upstream text"}},
        {"protocol_version": True, "scripts": []},
        {
            "protocol_version": 1,
            "scripts": [],
            "script_path": "/private/local.py",
        },
        {"protocol_version": 1, "scripts": None},
        [],
        42,
    ],
)
async def test_unsupported_or_invalid_clients_are_not_empty_success(
    monkeypatch, response
):
    project, device, connection, _, db = fixture(monkeypatch)
    connection.send_request.return_value = response
    with pytest.raises(HTTPException) as error:
        await module.DeviceScriptCatalogService(db).get_catalog(
            project, device
        )
    assert error.value.status_code in {409, 502}
    assert "private" not in str(error.value.detail)
