"""Persisted lifecycle errors preserve recovery facts without private text."""

from unittest.mock import AsyncMock, Mock

import pytest

from app.services import plugin_installation as installation
from app.services import plugin_upgrade as upgrade
from test_plugin_operations import (
    fixture as operation_fixture,
    upgrade_request,
)


PRIVATE = "synthetic-private-plugin-credential"
fixture = operation_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["install", "start"])
@pytest.mark.parametrize("raises", [False, True])
async def test_installation_state_and_public_error_never_store_raw_details(
    fixture,
    monkeypatch,
    phase,
    raises,
):
    config, row = fixture
    result = (False, PRIVATE, None) if phase == "install" else (False, PRIVATE)
    operation = (
        AsyncMock(side_effect=RuntimeError(PRIVATE))
        if raises
        else (AsyncMock(return_value=result))
    )
    if phase == "install":
        monkeypatch.setattr(installation.installer, "install", operation)
    else:
        monkeypatch.setattr(
            installation.process_manager, "start_plugin", operation
        )
    progress = AsyncMock()
    with pytest.raises(installation.PluginInstallationError) as error:
        await installation.install_plugin_operation(config, progress)
    assert PRIVATE not in str(error.value)
    assert row.status == "error" and PRIVATE not in row.last_error
    assert not any(
        call.args[0] == "complete" for call in progress.await_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    ["download", "new-start", "old-start", "rollback", "cleanup", "stop"],
)
async def test_upgrade_failure_keeps_truthful_safe_recovery_outcome(
    fixture,
    tmp_path,
    monkeypatch,
    phase,
):
    config, row = fixture
    (tmp_path / config.id).mkdir()
    if phase == "download":
        monkeypatch.setattr(
            upgrade.installer,
            "upgrade",
            AsyncMock(
                return_value=(False, PRIVATE, None),
            ),
        )
    elif phase == "cleanup":
        monkeypatch.setattr(
            upgrade.installer,
            "complete_upgrade",
            Mock(
                side_effect=RuntimeError(PRIVATE),
            ),
        )
    else:
        starts = [
            (False, PRIVATE),
            (False, PRIVATE) if phase == "old-start" else (True, "connected"),
        ]
        monkeypatch.setattr(
            upgrade.process_manager,
            "start_plugin",
            AsyncMock(
                side_effect=starts,
            ),
        )
        if phase == "rollback":
            monkeypatch.setattr(
                upgrade.installer,
                "rollback_upgrade",
                Mock(
                    side_effect=RuntimeError(PRIVATE),
                ),
            )
        elif phase == "stop":
            monkeypatch.setattr(
                upgrade.process_manager,
                "stop_plugin",
                AsyncMock(
                    side_effect=[True, False],
                ),
            )
    progress = AsyncMock()
    await upgrade.upgrade_plugin_operation(
        config.id,
        upgrade_request(config),
        config.project_id,
        progress,
    )
    messages = [call.args[1] for call in progress.await_args_list]
    assert all(PRIVATE not in message for message in messages)
    assert PRIVATE not in (row.last_error or "")
    assert progress.await_args.args[0] == "error"
    assert not any(
        call.args[0] == "complete" for call in progress.await_args_list
    )
    if phase in {"download", "new-start", "old-start"}:
        assert (
            row.version == "1" and row.runtime_config["env"]["VERSION"] == "1"
        )
        assert ("restored and connected" in messages[-1]) is (
            phase != "old-start"
        )
        assert ("restart failed" in messages[-1]) is (phase == "old-start")
    elif phase == "cleanup":
        assert row.version == "2" and "connected" in messages[-1]
        assert "cleanup failed" in messages[-1]
        upgrade.installer.rollback_upgrade.assert_not_called()
    elif phase == "stop":
        assert "backup preserved" in messages[-1]
        upgrade.installer.rollback_upgrade.assert_not_called()
    else:
        assert "recovery failed" in messages[-1].lower()
        assert "restored" not in messages[-1]
