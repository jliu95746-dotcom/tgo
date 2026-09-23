"""Alembic environment configuration."""

from logging.config import fileConfig

from alembic import context
from alembic.runtime.environment import NameFilterParentNames, NameFilterType
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

from app.core.config import settings
from app.core.database import Base

# Import all models to ensure they are registered with SQLAlchemy
from app.models import *  # noqa: F401, F403

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata


def include_name(
    name: str | None,
    type_: NameFilterType,
    parent_names: NameFilterParentNames,
) -> bool:
    """只管理 api_* 前缀的表，避免与 SaaS 迁移冲突。"""
    if type_ == "table":
        return name is not None and name.startswith("api_")
    return True

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_url() -> str:
    """Get database URL from settings."""
    url = str(settings.DATABASE_URL)
    # Convert async URL to sync for migrations
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://")
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="api_alembic_version",
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_on_connection(connection: Connection) -> None:
    """Use the caller's transaction for atomic application and dry runs."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table="api_alembic_version",
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Use a supplied connection or open the configured database."""
    connection = config.attributes.get("connection")
    if connection is not None:
        run_migrations_on_connection(connection)
        return
    connectable = create_engine(get_url(), poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            run_migrations_on_connection(connection)
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
