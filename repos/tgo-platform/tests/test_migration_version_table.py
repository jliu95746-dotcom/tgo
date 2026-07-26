from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from migrations.version_table import ensure_platform_version_table_capacity


def test_platform_version_table_accepts_long_revision_ids() -> None:
    connection = Mock(dialect=SimpleNamespace(name="postgresql"))

    ensure_platform_version_table_capacity(connection)

    statements = [str(call.args[0]) for call in connection.execute.call_args_list]
    assert any("VARCHAR(128)" in statement for statement in statements)
    assert any("ALTER TABLE pt_alembic_version" in statement for statement in statements)


def test_platform_version_table_preflight_is_postgres_only() -> None:
    connection = Mock(dialect=SimpleNamespace(name="sqlite"))

    ensure_platform_version_table_capacity(connection)

    connection.execute.assert_not_called()
