"""Clear expired encrypted bodies in bounded batches; retain metadata."""

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_device_script_case import AgentDeviceScriptCase
from app.models.agent_device_script_evidence import AgentDeviceScriptEvidence


async def purge_script_evidence(db: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    total = 0
    for model in (AgentDeviceScriptCase, AgentDeviceScriptEvidence):
        identifiers = (
            select(model.id)
            .where(
                model.expires_at <= now,
                model.ciphertext.is_not(None),
            )
            .limit(500)
        )
        rows = await db.scalars(
            update(model)
            .where(
                model.id.in_(identifiers),
                model.expires_at <= now,
                model.ciphertext.is_not(None),
            )
            .values(ciphertext=None, cleared_at=now)
            .returning(model.id)
        )
        total += len(rows.all())
    await db.commit()
    return total
