"""Committing a migration requires --apply after backup validation."""

from unittest.mock import MagicMock

import pytest

from app.commands import migrate_operators as command
from app.core import database


@pytest.mark.parametrize("apply", [False, True])
def test_command_commit_is_explicit(monkeypatch, apply, capsys):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    transaction = connection.begin.return_value
    monkeypatch.setattr(database, "sync_engine", engine)
    validate = MagicMock()
    monkeypatch.setattr(command, "validate_backup", validate)
    monkeypatch.setattr(command, "apply_operator_revision", MagicMock(
        return_value=True,
    ))
    args = ["migrate_operators", "--backup-manifest", "manifest.json"]
    if apply:
        args.append("--apply")
    monkeypatch.setattr(command.sys, "argv", args)
    assert command.main() == 0
    validate.assert_called_once()
    if apply:
        transaction.commit.assert_called_once()
        transaction.rollback.assert_not_called()
    else:
        transaction.commit.assert_not_called()
        transaction.rollback.assert_called_once()
    assert "PASS" in capsys.readouterr().out


def test_invalid_backup_never_opens_database(monkeypatch, capsys):
    engine = MagicMock()
    monkeypatch.setattr(database, "sync_engine", engine)
    monkeypatch.setattr(command, "validate_backup", MagicMock(
        side_effect=ValueError("private backup path"),
    ))
    monkeypatch.setattr(command.sys, "argv", [
        "migrate_operators", "--backup-manifest", "missing.json", "--apply",
    ])
    assert command.main() == 1
    engine.connect.assert_not_called()
    assert "private backup path" not in capsys.readouterr().err


def test_migration_failure_rolls_back(monkeypatch, capsys):
    engine = MagicMock()
    transaction = (
        engine.connect.return_value.__enter__.return_value.begin.return_value
    )
    monkeypatch.setattr(database, "sync_engine", engine)
    monkeypatch.setattr(command, "validate_backup", MagicMock())
    monkeypatch.setattr(command, "apply_operator_revision", MagicMock(
        side_effect=RuntimeError("sensitive database connection"),
    ))
    monkeypatch.setattr(command.sys, "argv", [
        "migrate_operators", "--backup-manifest", "manifest.json", "--apply",
    ])
    assert command.main() == 1
    transaction.rollback.assert_called_once()
    transaction.commit.assert_not_called()
    assert "sensitive database connection" not in capsys.readouterr().err
