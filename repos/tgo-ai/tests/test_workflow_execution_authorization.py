"""A queued workflow call must observe the employee's current authority."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.workflow import AgentWorkflow
from app.services.workflow_execution_authorization import require_current_binding


@pytest.mark.asyncio
async def test_queued_call_stops_after_employee_or_binding_changes(
    db_session: AsyncSession, test_project,
) -> None:
    agent = Agent(
        project_id=test_project.id, name="订单员工", model="fixture",
        is_active=True, is_default=False,
    )
    db_session.add(agent)
    await db_session.flush()
    binding = AgentWorkflow(
        agent_id=agent.id, workflow_id=str(uuid4()), enabled=True,
        release_version=2, release_id=str(uuid4()), release_digest="a" * 64,
    )
    db_session.add(binding)
    await db_session.commit()
    values = (
        str(agent.id), str(test_project.id), binding.workflow_id,
        2, binding.release_id, binding.release_digest,
    )

    await require_current_binding(db_session, *values)

    agent.is_active = False
    await db_session.commit()
    with pytest.raises(PermissionError, match="授权已变更"):
        await require_current_binding(db_session, *values)

    agent.is_active = True
    binding.enabled = False
    await db_session.commit()
    with pytest.raises(PermissionError, match="授权已变更"):
        await require_current_binding(db_session, *values)

    binding.enabled = True
    binding.release_version = 3
    await db_session.commit()
    with pytest.raises(PermissionError, match="授权已变更"):
        await require_current_binding(db_session, *values)

    binding.release_version = 2
    await db_session.commit()
    with pytest.raises(PermissionError, match="授权已变更"):
        await require_current_binding(
            db_session, str(agent.id), str(uuid4()), binding.workflow_id,
            2, binding.release_id, binding.release_digest,
        )
