"""The monitor is read-only and never reports query failures as healthy."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.commands import check_commercial_health as command
from app.services.commercial_health import CommercialHealthReport


@pytest.fixture
def database():
    db = MagicMock()
    db.in_transaction.return_value = False
    db.get_bind.return_value.dialect.name = "postgresql"
    return db


@pytest.mark.parametrize("failure", [False, True])
def test_check_uses_readonly_transaction_and_always_rolls_back(
    database, monkeypatch, failure
):
    scanner = MagicMock(side_effect=RuntimeError("failed") if failure else None)
    monkeypatch.setattr(command, "inspect_commercial_health", scanner)
    if failure:
        with pytest.raises(RuntimeError):
            command.run_check(database)
    else:
        command.run_check(database)
    assert [str(call.args[0]) for call in database.execute.call_args_list] == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        "SET LOCAL statement_timeout = '30s'",
    ]
    database.rollback.assert_called_once()
    database.commit.assert_not_called()


@pytest.mark.parametrize("case", ["transaction", "dialect"])
def test_unsafe_sessions_rejected(database, case):
    if case == "transaction":
        database.in_transaction.return_value = True
    else:
        database.get_bind.return_value.dialect.name = "sqlite"
    with pytest.raises(ValueError):
        command.run_check(database)
    database.execute.assert_not_called()


@pytest.mark.parametrize("status,code", [("clear", 0), ("attention", 2), ("error", 1)])
def test_exit_codes_and_redacted_failure(monkeypatch, capsys, status, code):
    from app.core import database as database_module

    monkeypatch.setattr(database_module, "SessionLocal", MagicMock())
    monkeypatch.setattr(command.sys, "argv", ["check_commercial_health"])
    scanner = MagicMock()
    if status == "error":
        scanner.side_effect = RuntimeError("synthetic-secret-password")
    else:
        scanner.return_value = CommercialHealthReport(
            checked_at=datetime.now(timezone.utc), status=status, counts={}
        )
    monkeypatch.setattr(command, "run_check", scanner)
    assert command.main() == code
    output = capsys.readouterr()
    assert "synthetic-secret-password" not in output.out + output.err
    if status == "error":
        assert output.out == ""
        assert "RuntimeError" in output.err
    else:
        assert '"will_change_data": false' in output.out
