"""Read the current row and current administrator even across retained ORM state."""

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.models.agent import Agent
from app.exceptions import AuthorizationError
from tests.test_agent_business_trials import trial


@pytest.mark.asyncio
async def test_snapshot_rereads_other_transactions_and_never_enables(trial):
    db, service, owner, fingerprint, project, actor, agent, request = trial
    first = await service.current_employee(project, actor, agent)
    assert not first.active and first.can_enable is False
    async with async_sessionmaker(db.bind, expire_on_commit=False)() as other:
        await other.execute(
            update(Agent).where(Agent.id == agent).values(is_active=True)
        )
        await other.commit()
    current = await service.current_employee(project, actor, agent)
    assert (
        current.active
        and current.actor_id == actor
        and current.agent_id == agent
        and current.can_enable is False
    )
    assert set(current.model_dump()) == {
        "scope",
        "can_enable",
        "project_id",
        "actor_id",
        "agent_id",
        "active",
        "checked_at",
    }
    owner.get_agent_configuration_actor.side_effect = AuthorizationError("revoked")
    with pytest.raises(AuthorizationError):
        await service.current_employee(project, actor, agent)
