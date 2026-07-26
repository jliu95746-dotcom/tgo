from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import context

# Ensure project root is on sys.path so 'app' is importable when running via Alembic
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Import application models and settings
from app.db.models import Base
from app.core.config import settings
from migrations.version_table import ensure_platform_version_table_capacity

# this is the Alembic Config object, which provides access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
# target_metadata = None

target_metadata = Base.metadata


# Limit autogenerate scope to platform tables only
_PT_TABLES = {"pt_platforms", "pt_email_inbox", "pt_wecom_inbox", "pt_wukongim_inbox"}

def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in _PT_TABLES or name.startswith("pt_")
    return True


def include_name(name, type_, parent_names):
    if type_ == "table":
        return name in _PT_TABLES or name.startswith("pt_")
    return True

# Prune autogenerate ops that try to touch non-platform tables
try:
    from alembic.operations import ops as _alembic_ops
except Exception:  # pragma: no cover
    _alembic_ops = None


def process_revision_directives(context, revision, directives):
    if not getattr(context.config, "cmd_opts", None):
        return
    if not getattr(context.config.cmd_opts, "autogenerate", False):
        return
    if not directives:
        return
    script = directives[0]
    if not hasattr(script, "upgrade_ops"):
        return
    if _alembic_ops is None:
        return

    def _keep(op):
        try:
            if isinstance(op, _alembic_ops.DropTableOp):
                return op.table_name in _PT_TABLES or op.table_name.startswith("pt_")
            if isinstance(op, _alembic_ops.DropIndexOp):
                tname = getattr(op, "table_name", None)
                return not tname or tname in _PT_TABLES or tname.startswith("pt_")
        except Exception:
            return True
        return True

    try:
        script.upgrade_ops.ops = [op for op in script.upgrade_ops.ops if _keep(op)]
    except Exception:
        pass

# other values from the config, defined by the needs of env.py, can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine, though an Engine is acceptable here as well.
    By skipping the Engine creation we don't even need a DBAPI to be available.
    Calls to context.execute() here emit the given string to the script output.
    """

    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        version_table="pt_alembic_version",
        include_object=include_object,
        include_name=include_name,
        process_revision_directives=process_revision_directives,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        version_table="pt_alembic_version",
        include_object=include_object,
        include_name=include_name,
        process_revision_directives=process_revision_directives,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an AsyncEngine."""

    connectable: AsyncEngine = create_async_engine(settings.database_url, poolclass=pool.NullPool)

    async def run() -> None:
        # The compatibility DDL starts its own transaction. Commit it before
        # Alembic opens the migration transaction, otherwise SQLAlchemy's
        # autobegin transaction is rolled back when the connection closes.
        async with connectable.begin() as connection:
            await connection.run_sync(ensure_platform_version_table_capacity)

        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)

    asyncio.run(run())


def run_migrations() -> None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()


run_migrations()

