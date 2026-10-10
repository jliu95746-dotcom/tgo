"""Serialize account activation and retire peers in the same transaction."""
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent


async def lock_account_agents(db: AsyncSession, project_id: UUID) -> None:
    if db.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(project_id.bytes[:8], "big", signed=True)
        await db.execute(select(func.pg_advisory_xact_lock(key)))


async def deactivate_account_peers(
    db: AsyncSession, project_id: UUID, agent_id: UUID | None = None
) -> None:
    conditions = [
        Agent.project_id == project_id,
        Agent.deleted_at.is_(None),
        (Agent.is_active.is_(True) | Agent.is_default.is_(True)),
    ]
    if agent_id is not None:
        conditions.append(Agent.id != agent_id)
    await db.execute(
        update(Agent)
        .where(*conditions)
        .values(is_active=False, is_default=False)
    )
