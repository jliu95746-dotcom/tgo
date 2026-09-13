"""Exercise the real migration on an isolated database, retaining employee rows."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_activation_upgrade_and_rollback_preserve_existing_employees() -> None:
    path = (
        Path(__file__).parents[1]
        / "migrations/versions/n6o7p8q9r0s1_add_agent_activation.py"
    )
    spec = spec_from_file_location("activation_migration", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE ai_agents (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
                )
            )
            connection.execute(
                text("INSERT INTO ai_agents (id, name) VALUES (1, 'existing')")
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert (
                    connection.execute(
                        text("SELECT is_active FROM ai_agents WHERE id = 1")
                    ).scalar()
                    == 1
                )
                connection.execute(
                    text("INSERT INTO ai_agents (id, name) VALUES (2, 'new')")
                )
                assert (
                    connection.execute(
                        text("SELECT is_active FROM ai_agents WHERE id = 2")
                    ).scalar()
                    == 1
                )
                connection.execute(
                    text("UPDATE ai_agents SET is_active = 0 WHERE id = 1")
                )
                assert (
                    connection.execute(
                        text("SELECT is_active FROM ai_agents WHERE id = 1")
                    ).scalar()
                    == 0
                )
                migration.downgrade()
                assert "is_active" not in {
                    column["name"]
                    for column in inspect(connection).get_columns("ai_agents")
                }
                assert connection.execute(
                    text("SELECT name FROM ai_agents ORDER BY id")
                ).scalars().all() == ["existing", "new"]
    finally:
        engine.dispose()
