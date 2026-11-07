"""Plugin failure outputs keep credentials out of progress and state."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import routes
from app.schemas.install import PluginBuildConfig, PluginInstallRequest
from app.schemas.install import PluginUpgradeRequest
from app.services import installer as installer_module
from app.services import process_manager as process_module
from app.services.installer import PluginInstaller
from app.services.plugin_installation import PluginInstallationError


PRIVATE = "synthetic-private-plugin-credential"


def config():
    return PluginInstallRequest(
        id="fixture",
        project_id=str(uuid4()),
        name="Fixture",
        version="1",
        source={"binary": {"url": "https://source.test/plugin.zip"}},
        source_url="https://source.test/plugin.yaml",
    )


def assert_safe_logs(caplog):
    records = [
        record
        for record in caplog.records
        if record.name.startswith(
            (
                "api.routes",
                "services.installer",
                "services.process_manager",
                "services.plugin_failure",
            ),
        )
    ]
    assert all(PRIVATE not in str(record.__dict__) for record in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["install", "upgrade"])
async def test_streamed_unexpected_failure_has_no_private_error(
    monkeypatch,
    caplog,
    kind,
):
    request = config()
    operation = AsyncMock(side_effect=RuntimeError(PRIVATE))
    if kind == "install":
        monkeypatch.setattr(routes, "install_plugin_operation", operation)
        response = await routes.install_plugin_stream(request)
    else:
        monkeypatch.setattr(routes, "upgrade_plugin_operation", operation)
        response = await routes.upgrade_plugin(
            "fixture",
            PluginUpgradeRequest(
                latest_config=request.model_dump(
                    exclude={"project_id"},
                )
            ),
            request.project_id,
        )
    wire = "".join([chunk async for chunk in response.body_iterator])
    event = json.loads(wire.removeprefix("data:").strip())
    assert event["stage"] == "error" and PRIVATE not in event["message"]
    assert '"stage": "complete"' not in wire
    assert_safe_logs(caplog)
    operation.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,expected",
    [
        (RuntimeError(PRIVATE), 500),
        (ValueError(PRIVATE), 400),
        (PluginInstallationError(PRIVATE, 409), 409),
    ],
)
async def test_regular_install_does_not_echo_unexpected_or_dynamic_details(
    monkeypatch,
    caplog,
    error,
    expected,
):
    monkeypatch.setattr(
        routes,
        "install_plugin_operation",
        AsyncMock(side_effect=error),
    )
    with pytest.raises(HTTPException) as raised:
        await routes.install_plugin(config())
    assert raised.value.status_code == expected
    assert PRIVATE not in str(raised.value.detail)
    assert_safe_logs(caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,expected", [(ValueError, 400), (RuntimeError, 500)]
)
async def test_source_resolution_failure_keeps_private_details_server_side(
    monkeypatch,
    caplog,
    error,
    expected,
):
    monkeypatch.setattr(
        routes.PluginURLResolver,
        "resolve",
        AsyncMock(side_effect=error(PRIVATE)),
    )
    with pytest.raises(HTTPException) as raised:
        await routes.fetch_plugin_info(
            routes.PluginFetchRequest(url="https://source.test/plugin.yaml")
        )
    assert raised.value.status_code == expected
    assert PRIVATE not in str(raised.value.detail)
    assert_safe_logs(caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["install", "upgrade"])
async def test_unexpected_installer_failure_preserves_files_without_raw_errors(
    tmp_path,
    monkeypatch,
    caplog,
    method,
):
    installer = PluginInstaller(str(tmp_path))
    if method == "install":
        monkeypatch.setattr(
            installer,
            "_install_from_binary",
            AsyncMock(
                side_effect=RuntimeError(PRIVATE),
            ),
        )
    else:
        (tmp_path / "fixture").mkdir()
        (tmp_path / "fixture" / "original").write_text("previous")
        monkeypatch.setattr(
            installer,
            "install",
            AsyncMock(side_effect=RuntimeError(PRIVATE)),
        )
    success, message, path = await getattr(installer, method)(config())
    assert not success and path is None and PRIVATE not in message
    if method == "upgrade":
        assert (tmp_path / "fixture" / "original").read_text() == "previous"
    else:
        assert not (tmp_path / "fixture").exists()
    assert_safe_logs(caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["git", "go", "venv", "pip", "npm"])
async def test_external_tool_stderr_is_not_a_public_failure_message(
    tmp_path,
    monkeypatch,
    caplog,
    stage,
):
    installer = PluginInstaller(str(tmp_path))
    installer._ensure_dirs()
    target = tmp_path / "fixture"
    target.mkdir()
    failed = SimpleNamespace(
        returncode=1,
        communicate=AsyncMock(
            return_value=(b"", PRIVATE.encode()),
        ),
    )
    succeeded = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(
            return_value=(b"", b""),
        ),
    )
    spawn = AsyncMock(
        side_effect=[succeeded, failed] if stage == "pip" else [failed]
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    report = AsyncMock()
    if stage == "git":
        success, message = await installer._install_from_github(
            "fixture",
            target,
            SimpleNamespace(repo="example/fixture", ref=PRIVATE, path="/"),
            None,
            report,
        )
        assert PRIVATE in spawn.await_args.args
    else:
        language = {
            "go": "go",
            "venv": "python",
            "pip": "python",
            "npm": "nodejs",
        }[stage]
        build = PluginBuildConfig.model_validate(
            {
                "language": language,
                language: {},
            }
        )
        if stage == "pip":
            (target / "requirements.txt").write_text("fixture")
            monkeypatch.setattr(
                installer_module,
                "plugin_python",
                lambda *args, **kwargs: "python",
            )
        if stage == "npm":
            (target / "package.json").write_text("{}")
        success, message = await installer._build_plugin(
            "fixture",
            target,
            build,
            report,
        )
    assert not success and PRIVATE not in message
    assert all(
        PRIVATE not in str(call.args) for call in report.await_args_list
    )
    assert_safe_logs(caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["preflight", "spawn"])
async def test_process_failure_state_does_not_store_private_exception(
    tmp_path,
    monkeypatch,
    caplog,
    phase,
):
    manager = process_module.ProcessManager(str(tmp_path))
    update = AsyncMock()
    monkeypatch.setattr(manager, "_update_db_status", update)
    if phase == "preflight":
        manager._managed_plugins["fixture"] = process_module.ManagedPlugin(
            id="fixture",
            config={},
        )
        monkeypatch.setattr(
            manager,
            "_launch_plugin",
            AsyncMock(
                side_effect=RuntimeError(PRIVATE),
            ),
        )
    else:
        (tmp_path / "fixture").mkdir()
        (tmp_path / "fixture" / "plugin").write_bytes(b"fixture")
        monkeypatch.setattr(
            asyncio,
            "create_subprocess_exec",
            AsyncMock(
                side_effect=OSError(PRIVATE),
            ),
        )
        monkeypatch.setattr(
            manager,
            "_stop_plugin_inner",
            AsyncMock(
                return_value=True,
            ),
        )
    success, message = await manager.start_plugin("fixture", {})
    assert not success and PRIVATE not in message
    status = manager.get_status("fixture")
    assert status["status"] == "error" and PRIVATE not in status["last_error"]
    assert all(
        PRIVATE not in str(call.kwargs) for call in update.await_args_list
    )
    assert_safe_logs(caplog)
