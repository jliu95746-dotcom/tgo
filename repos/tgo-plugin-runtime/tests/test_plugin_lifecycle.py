"""Lifecycle routes report the actual result and preserve process ownership."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.api import routes
from app.schemas.install import PluginInstallRequest
from app.services import process_manager as process_module
from app.services.plugin_installation import PluginInstallationError


@pytest.mark.asyncio
async def test_install_stream_never_emits_complete_when_start_failed(monkeypatch):
    operation = AsyncMock(
        side_effect=PluginInstallationError("Plugin exited before registration")
    )
    monkeypatch.setattr(routes, "install_plugin_operation", operation)
    request = PluginInstallRequest(
        id="fixture",
        project_id=str(uuid4()),
        name="fixture",
        version="1",
        source={"binary": {"url": "http://127.0.0.1/fixture.zip"}},
    )
    response = await routes.install_plugin_stream(request)
    output = "".join([item async for item in response.body_iterator])
    assert '"stage": "error"' in output
    assert '"stage": "complete"' not in output
    operation.assert_awaited_once()


@pytest.mark.asyncio
async def test_early_process_exit_is_a_failed_start(tmp_path, monkeypatch):
    folder = tmp_path / "fixture"
    folder.mkdir()
    manager = process_module.ProcessManager(str(tmp_path))
    monkeypatch.setattr(manager, "_update_db_status", AsyncMock())
    monkeypatch.setattr(
        process_module.plugin_manager, "get_plugin", Mock(return_value=None)
    )
    # A nonexistent script is handled by the actual Python subprocess, not a mock.
    success, message = await manager.start_plugin(
        "fixture",
        {
            "build": {"language": "python", "python": {"entrypoint": "missing.py"}},
            "runtime": {"auto_restart": False},
        },
    )
    assert not success and "exited before registration" in message
    assert manager.get_status("fixture")["status"] == "error"
    assert manager.get_status("fixture")["pid"] is None


@pytest.mark.asyncio
async def test_stop_cancels_delayed_automatic_restart(tmp_path, monkeypatch):
    manager = process_module.ProcessManager(str(tmp_path))
    managed = process_module.ManagedPlugin(id="fixture", config={}, status="error")
    manager._managed_plugins["fixture"] = managed
    managed._restart_task = asyncio.create_task(
        manager._delayed_restart("fixture", 0.05)
    )
    start = AsyncMock()
    monkeypatch.setattr(manager, "_start_plugin_inner", start)
    monkeypatch.setattr(manager, "_update_db_status", AsyncMock())
    monkeypatch.setattr(
        process_module.plugin_manager, "get_plugin", Mock(return_value=None)
    )
    await manager.stop_plugin("fixture")
    await asyncio.sleep(0.1)
    start.assert_not_awaited()
    assert managed.status == "stopped"


@pytest.mark.asyncio
async def test_old_connection_cleanup_cannot_unregister_new_connection(monkeypatch):
    manager = process_module.plugin_manager
    old, current = SimpleNamespace(), SimpleNamespace()
    monkeypatch.setattr(manager, "_plugins", {"fixture": current})
    await manager.unregister("fixture", expected_connection=old)
    assert manager.get_plugin("fixture") is current


@pytest.mark.asyncio
async def test_preflight_failure_sets_error_without_escaping(tmp_path, monkeypatch):
    manager = process_module.ProcessManager(str(tmp_path))
    (tmp_path / "fixture").mkdir()
    status = AsyncMock()
    monkeypatch.setattr(manager, "_update_db_status", status)
    success, _ = await manager.start_plugin(
        "fixture",
        {
            "build": {"language": "python", "python": {"entrypoint": "../outside.py"}},
        },
    )
    assert not success and manager.get_status("fixture")["status"] == "error"
    assert status.await_args.args[1] == "error"


@pytest.mark.asyncio
async def test_service_shutdown_preserves_restart_intent(tmp_path, monkeypatch):
    manager = process_module.ProcessManager(str(tmp_path))
    manager._managed_plugins["fixture"] = process_module.ManagedPlugin(
        id="fixture", config={}, status="running"
    )
    status = AsyncMock()
    monkeypatch.setattr(manager, "_update_db_status", status)
    monkeypatch.setattr(
        process_module.plugin_manager, "get_plugin", Mock(return_value=None)
    )
    await manager.stop()
    assert status.await_args.args == ("fixture", "starting")
