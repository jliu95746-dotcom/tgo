"""Online state follows live project-bound connections, not stale database flags."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.device import Device, DeviceStatus, DeviceType
from app.services.device_service import DeviceService
from app.services.tcp_connection_manager import tcp_connection_manager


@pytest.mark.asyncio
@pytest.mark.parametrize("connected,stored", [(False, DeviceStatus.ONLINE), (True, DeviceStatus.OFFLINE)])
async def test_device_response_uses_current_presence(monkeypatch, connected, stored):
    project, device_id = uuid4(), uuid4()
    device = Device(
        id=device_id,
        project_id=project,
        device_name="Fixture",
        os="test",
        device_type=DeviceType.DESKTOP,
        status=stored,
        created_at=datetime.now(timezone.utc),
    )
    connection = SimpleNamespace(
        agent_id=str(device_id), project_id=str(project), last_seen=datetime.now(timezone.utc)
    )
    monkeypatch.setattr(tcp_connection_manager, "list_connections", lambda: [connection] if connected else [])
    result = MagicMock()
    result.scalar_one_or_none.return_value = device
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    response = await DeviceService(db).get_device(device_id, project)
    assert response.status == ("online" if connected else "offline")
    assert device.status == stored, "presence read must not rewrite historical records"


@pytest.mark.asyncio
async def test_connection_list_is_scoped_and_excludes_deleted_records(monkeypatch):
    project, device_id = uuid4(), uuid4()
    connection = SimpleNamespace(
        agent_id=str(device_id),
        project_id=str(project),
        name="old-name",
        version="1",
        capabilities=["tools/list"],
        tools=[],
        connected_at=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
    )
    foreign = SimpleNamespace(agent_id=str(uuid4()), project_id=str(uuid4()))
    monkeypatch.setattr(tcp_connection_manager, "list_connections", lambda: [connection, foreign])
    row = SimpleNamespace(id=device_id, device_name="current-name")
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    service = DeviceService(db)
    response = await service.list_connected_devices(project)
    assert response.count == 1 and response.devices[0].device_id == device_id
    assert response.devices[0].name == "current-name"
    query = db.execute.call_args.args[0]
    assert query.compile().params["project_id_1"] == project
    result.scalars.return_value.all.return_value = []
    assert (await service.list_connected_devices(project)).count == 0
