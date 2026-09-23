"""The maintenance entry point must enforce a read-only snapshot."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.commands import audit_chat_files as command
from app.services.chat_file_audit import AttachmentAuditReport


@pytest.fixture
def database():
    db = MagicMock()
    db.in_transaction.return_value = False
    db.get_bind.return_value.dialect.name = "postgresql"
    return db


def run(db):
    return command.run_audit(
        db,
        storage_type="local",
        upload_root=Path("uploads"),
        sample_limit=20,
    )


def test_command_uses_read_only_snapshot_and_rollback(database, monkeypatch):
    report = AttachmentAuditReport(storage_type="local")
    scan = MagicMock(return_value=report)
    monkeypatch.setattr(command, "audit_chat_files", scan)
    assert run(database) is report
    sql = [str(call.args[0]) for call in database.execute.call_args_list]
    assert sql == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        "SET LOCAL statement_timeout = '30s'",
    ]
    scan.assert_called_once()
    database.rollback.assert_called_once()
    database.commit.assert_not_called()


def test_scan_failure_still_rolls_back(database, monkeypatch):
    monkeypatch.setattr(
        command,
        "audit_chat_files",
        MagicMock(
            side_effect=RuntimeError("query failed"),
        ),
    )
    with pytest.raises(RuntimeError):
        run(database)
    database.rollback.assert_called_once()
    database.commit.assert_not_called()


@pytest.mark.parametrize("case", ["transaction", "dialect"])
def test_command_refuses_unsafe_session(database, case):
    if case == "transaction":
        database.in_transaction.return_value = True
    else:
        database.get_bind.return_value.dialect.name = "sqlite"
    with pytest.raises(ValueError):
        run(database)
    database.execute.assert_not_called()


@pytest.mark.parametrize("flagged,exit_code", [(0, 0), (1, 2)])
def test_cli_exit_status_and_json(
    database, monkeypatch, capsys, flagged, exit_code
):
    from app.core import database as database_module

    session = MagicMock()
    session.return_value.__enter__.return_value = database
    monkeypatch.setattr(database_module, "SessionLocal", session)
    monkeypatch.setattr(command.sys, "argv", ["audit_chat_files"])
    monkeypatch.setattr(
        command,
        "run_audit",
        MagicMock(
            return_value=(
                AttachmentAuditReport(
                    storage_type="local", flagged_records=flagged
                )
            )
        ),
    )
    assert command.main() == exit_code
    assert '"will_change_data": false' in capsys.readouterr().out


def test_cli_failure_never_prints_sensitive_exception(
    database, monkeypatch, capsys
):
    from app.core import database as database_module

    session = MagicMock()
    session.return_value.__enter__.return_value = database
    monkeypatch.setattr(database_module, "SessionLocal", session)
    monkeypatch.setattr(command.sys, "argv", ["audit_chat_files"])
    monkeypatch.setattr(
        command,
        "run_audit",
        MagicMock(
            side_effect=RuntimeError(
                "postgresql://secret-password@private-host"
            ),
        ),
    )
    assert command.main() == 1
    output = capsys.readouterr()
    assert "RuntimeError" in output.err
    assert "secret-password" not in output.err + output.out
