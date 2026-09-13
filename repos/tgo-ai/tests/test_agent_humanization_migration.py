"""Migration is additive and reversible without removing employee records."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text, inspect


def test_style_migration_preserves_employee_rows():
    path = Path(__file__).parents[1] / "migrations/versions/o7p8q9r0s1t2_add_agent_humanization_binding.py"
    spec = spec_from_file_location("style_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE ai_agents (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"))
        conn.execute(text("INSERT INTO ai_agents VALUES (1, 'existing')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert conn.execute(text("SELECT humanization_skill_name, humanization_skill_enabled FROM ai_agents")).one() == (None, 0)
            migration.downgrade()
            assert "humanization_skill_name" not in {column["name"] for column in inspect(conn).get_columns("ai_agents")}
            assert conn.execute(text("SELECT name FROM ai_agents")).scalar_one() == "existing"
    engine.dispose()
