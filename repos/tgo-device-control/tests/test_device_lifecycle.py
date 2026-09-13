"""Device deletion and disconnect must share the connection lifecycle boundary."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.device import DeviceStatus
from app.services import device_service as service_module
from app.services.device_service import DeviceService
from app.services.tcp_connection_manager import tcp_connection_manager


def database(device):
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = device
    db.execute = AsyncMock(return_value=result)
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["delete_device", "disconnect_device"])
async def test_owner_disconnects_and_persists(action, monkeypatch):
    device_id, project_id = uuid.uuid4(), uuid.uuid4()
    device = SimpleNamespace(id=device_id, status=DeviceStatus.ONLINE)
    db = database(device)
    disconnect = AsyncMock()
    monkeypatch.setattr(tcp_connection_manager, "unregister_connection", disconnect)
    result = await getattr(DeviceService(db), action)(device_id, project_id)
    assert result is True
    db.commit.assert_awaited_once()
    disconnect.assert_awaited_once_with(str(device_id))
    if action == "delete_device":
        db.delete.assert_awaited_once_with(device)
    else:
        assert device.status == DeviceStatus.OFFLINE


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["delete_device", "disconnect_device"])
async def test_foreign_or_missing_device_does_not_disconnect(action, monkeypatch):
    db = database(None)
    disconnect = AsyncMock()
    monkeypatch.setattr(tcp_connection_manager, "unregister_connection", disconnect)
    assert not await getattr(DeviceService(db), action)(uuid.uuid4(), uuid.uuid4())
    disconnect.assert_not_awaited()
    db.commit.assert_not_awaited()
    statement = str(db.execute.call_args.args[0])
    assert "dc_devices.project_id =" in statement
    assert "dc_devices.id =" in statement


@pytest.mark.asyncio
async def test_delete_waits_for_connection_lifecycle_lock(monkeypatch):
    device_id = uuid.uuid4()
    db = database(SimpleNamespace(id=device_id))
    monkeypatch.setattr(tcp_connection_manager, "unregister_connection", AsyncMock())
    async with tcp_connection_manager.lifecycle_lock(str(device_id)):
        task = asyncio.create_task(
            DeviceService(db).delete_device(device_id, uuid.uuid4())
        )
        await asyncio.sleep(0)
        db.delete.assert_not_awaited()
    assert await asyncio.wait_for(task, 1)


@pytest.mark.asyncio
async def test_failed_delete_keeps_connection(monkeypatch):
    db = database(SimpleNamespace(id=uuid.uuid4()))
    db.commit.side_effect = RuntimeError("write failed")
    disconnect = AsyncMock()
    monkeypatch.setattr(tcp_connection_manager, "unregister_connection", disconnect)
    with pytest.raises(RuntimeError, match="write failed"):
        await DeviceService(db).delete_device(uuid.uuid4(), uuid.uuid4())
    disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_registration_is_offline_until_connection_installed(monkeypatch):
    db = database(None)
    db.refresh = AsyncMock()
    monkeypatch.setattr(
        service_module.bind_code_service,
        "validate",
        AsyncMock(return_value=uuid.uuid4()),
    )
    device = await DeviceService(db).register_device(
        "ABC123", "Fixture", "desktop", "test", None, None
    )
    assert device.status == DeviceStatus.OFFLINE
    assert device.last_seen_at is None
