"""Install/upgrade ownership and failure state regression checks."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import asyncio
import httpx
import pytest

from app.models.plugin import InstalledPlugin
from app.schemas.install import PluginInstallRequest, PluginUpgradeRequest
from app.services import plugin_installation as installation
from app.services import plugin_upgrade as upgrade


class Session:
    def __init__(self, row):
        self.row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def execute(self, _):
        return SimpleNamespace(
            scalar_one=lambda: self.row, scalar_one_or_none=lambda: self.row
        )

    def add(self, row):
        self.row = row

    async def commit(self):
        pass


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    project_id = uuid4()
    config = PluginInstallRequest(
        id="fixture",
        project_id=str(project_id),
        name="previous",
        version="1",
        source={"binary": {"url": "http://127.0.0.1/plugin.zip"}},
        source_url="http://127.0.0.1/plugin.yaml",
        build={"language": "python", "python": {"entrypoint": "main.py"}},
        runtime={"env": {"VERSION": "1"}},
    )
    row = InstalledPlugin(
        id=uuid4(),
        plugin_id=config.id,
        project_id=project_id,
        installed_at=datetime.now(),
        updated_at=datetime.now(),
        install_path=str(tmp_path / config.id),
        status="running",
    )
    installation.apply_plugin_config(row, config)
    session = Session(row)
    for module in (installation, upgrade):
        monkeypatch.setattr(module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(installation.installer, "base_path", tmp_path)
    monkeypatch.setattr(
        installation.installer,
        "install",
        AsyncMock(return_value=(True, "installed", row.install_path)),
    )
    monkeypatch.setattr(
        upgrade.installer,
        "upgrade",
        AsyncMock(return_value=(True, "installed", row.install_path)),
    )
    monkeypatch.setattr(upgrade.installer, "complete_upgrade", Mock())
    monkeypatch.setattr(upgrade.installer, "rollback_upgrade", Mock(return_value=True))
    monkeypatch.setattr(
        upgrade.process_manager, "stop_plugin", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        upgrade.process_manager,
        "start_plugin",
        AsyncMock(return_value=(True, "connected")),
    )
    return config, row


@pytest.mark.asyncio
async def test_install_cannot_take_over_another_project(fixture):
    config, row = fixture
    request = config.model_copy(update={"project_id": str(uuid4()), "name": "attacker"})
    with pytest.raises(installation.PluginInstallationError) as raised:
        await installation.install_plugin_operation(request)
    assert raised.value.status_code == 409
    assert row.name == "previous"
    installation.installer.install.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result", [(False, "process exited"), RuntimeError("preflight failed")]
)
async def test_start_failure_is_durable_error_not_complete(
    fixture, monkeypatch, result
):
    config, row = fixture
    start = (
        AsyncMock(side_effect=result)
        if isinstance(result, Exception)
        else AsyncMock(return_value=result)
    )
    monkeypatch.setattr(installation.process_manager, "start_plugin", start)
    progress = AsyncMock()
    with pytest.raises(installation.PluginInstallationError):
        await installation.install_plugin_operation(config, progress)
    assert row.status == "error" and row.last_error and row.pid is None
    assert not any(call.args[0] == "complete" for call in progress.await_args_list)


def upgrade_request(config):
    content = config.model_dump(exclude={"project_id"})
    content.update(name="new", version="2", runtime={"env": {"VERSION": "2"}})
    return PluginUpgradeRequest(latest_config=content)


@pytest.mark.asyncio
async def test_upgrade_persists_new_runtime_only_after_files_ready(fixture, tmp_path):
    config, row = fixture
    (tmp_path / config.id).mkdir()
    progress = AsyncMock()
    await upgrade.upgrade_plugin_operation(
        config.id, upgrade_request(config), config.project_id, progress
    )
    assert row.version == "2" and row.runtime_config["env"]["VERSION"] == "2"
    upgrade.installer.complete_upgrade.assert_called_once_with(config.id)
    assert progress.await_args.args[0] == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "old_start", [(True, "connected"), (False, "old version failed")]
)
async def test_failed_upgrade_restores_config_and_reports_actual_restart(
    fixture, tmp_path, monkeypatch, old_start
):
    config, row = fixture
    (tmp_path / config.id).mkdir()
    monkeypatch.setattr(
        upgrade.process_manager,
        "start_plugin",
        AsyncMock(side_effect=[(False, "new version failed"), old_start]),
    )
    progress = AsyncMock()
    await upgrade.upgrade_plugin_operation(
        config.id, upgrade_request(config), config.project_id, progress
    )
    assert row.version == "1" and row.runtime_config["env"]["VERSION"] == "1"
    upgrade.installer.rollback_upgrade.assert_called_once_with(config.id)
    upgrade.installer.complete_upgrade.assert_not_called()
    stage, message = progress.await_args.args
    assert stage == "error"
    assert ("restored and connected" in message) is old_start[0]
    assert ("restart failed" in message) is not old_start[0]


@pytest.mark.asyncio
async def test_no_backup_does_not_claim_recovery(fixture, tmp_path, monkeypatch):
    config, _ = fixture
    (tmp_path / config.id).mkdir()
    monkeypatch.setattr(
        upgrade.process_manager,
        "start_plugin",
        AsyncMock(return_value=(False, "failed")),
    )
    monkeypatch.setattr(upgrade.installer, "rollback_upgrade", Mock(return_value=False))
    progress = AsyncMock()
    await upgrade.upgrade_plugin_operation(
        config.id, upgrade_request(config), config.project_id, progress
    )
    assert "recovery not confirmed" in progress.await_args.args[1]
    assert "restored" not in progress.await_args.args[1]


@pytest.mark.asyncio
async def test_lifecycle_api_waits_for_current_install_or_upgrade(fixture, monkeypatch):
    from app.api import routes
    from app.main import app

    config, row = fixture
    monkeypatch.setattr(routes, "AsyncSessionLocal", lambda: Session(row))
    monkeypatch.setattr(routes.process_manager, "get_status", Mock(return_value={"status": "stopped"}))
    lock = installation.plugin_lifecycle_lock(config.id)
    await lock.acquire()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        task = asyncio.create_task(client.post(f"/plugins/{config.id}/stop"))
        try:
            await asyncio.sleep(0.05)
            routes.process_manager.stop_plugin.assert_not_awaited()
        finally:
            lock.release()
        response = await asyncio.wait_for(task, timeout=2)
        assert response.status_code == 200
        routes.process_manager.stop_plugin.assert_awaited_once_with(config.id)


@pytest.mark.asyncio
async def test_explicit_start_config_cannot_bypass_project_check(fixture, monkeypatch):
    from app.api import routes
    from app.main import app

    config, _ = fixture
    monkeypatch.setattr(routes, "AsyncSessionLocal", lambda: Session(None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/plugins/{config.id}/start", json=config.model_dump(), params={"project_id": str(uuid4())})
    assert response.status_code == 404
    routes.process_manager.start_plugin.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_failure_reports_recovery_before_terminal_error(fixture, tmp_path, monkeypatch):
    config, row = fixture
    (tmp_path / config.id).mkdir()
    monkeypatch.setattr(upgrade.installer, "upgrade", AsyncMock(return_value=(False, "download failed", None)))
    progress = AsyncMock()
    await upgrade.upgrade_plugin_operation(config.id, upgrade_request(config), config.project_id, progress)
    assert row.version == "1"
    assert progress.await_args.args == ("error", "Upgrade failed: download failed. Original version restored and connected")
    upgrade.installer.complete_upgrade.assert_not_called()
