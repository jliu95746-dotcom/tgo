"""A legacy workflow pin is an explicit, durable, project-scoped operation."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.exceptions import ConflictError, ExternalServiceError, NotFoundError
from app.models.agent import Agent
from app.models.workflow import AgentWorkflow
from app.models.workflow_pin_transition import AgentWorkflowPinTransition
from app.schemas.agent_configuration_actor import AgentConfigurationActor
from app.schemas.workflow_pin_transition import WorkflowPinTransitionCommand
from app.services.workflow_pin_transition import WorkflowPinTransitionService
from app.services.workflow_service import WorkflowIntegrationRelease


def make_owner(project, actor):
    return type(
        "Owner",
        (),
        {
            "get_agent_configuration_actor": AsyncMock(
                return_value=AgentConfigurationActor(
                    project_id=project, requested_by=actor, checked_at=datetime.now(UTC)
                )
            )
        },
    )()


def make_resolver(workflow_id, version):
    release = WorkflowIntegrationRelease(
        workflow_id=workflow_id,
        release_version=version,
        release_id=str(uuid4()),
        definition_digest="b" * 64,
    )
    return (
        type(
            "Resolver",
            (),
            {
                "resolve_published_bindings": AsyncMock(
                    return_value={workflow_id: release}
                )
            },
        )(),
        release,
    )


@pytest.mark.asyncio
async def test_legacy_pin_needs_disabled_binding_and_records_one_transition(
    db_session,
    test_project,
):
    project, actor, workflow_id = test_project.id, uuid4(), str(uuid4())
    agent = Agent(
        id=uuid4(),
        project_id=project,
        name="仍在接待",
        model="fixture",
        is_active=True,
        is_default=True,
    )
    binding = AgentWorkflow(agent_id=agent.id, workflow_id=workflow_id, enabled=True)
    agent.workflows = [binding]
    db_session.add(agent)
    await db_session.commit()
    owner, (resolver, release) = make_owner(project, actor), make_resolver(
        workflow_id, 3
    )
    service = WorkflowPinTransitionService(db_session, owner=owner, resolver=resolver)
    command = WorkflowPinTransitionCommand(
        request_id=uuid4(),
        expected_binding_id=binding.id,
        release_version=3,
        confirmed_disabled=True,
    )

    with pytest.raises(ConflictError, match="停用"):
        await service.pin(project, actor, agent.id, workflow_id, command)
    await db_session.rollback()
    binding.enabled = False
    await db_session.commit()

    receipt = await service.pin(project, actor, agent.id, workflow_id, command)
    assert receipt.agent_id == agent.id
    assert receipt.binding_id == binding.id
    assert receipt.release_version == 3
    assert receipt.release_id == release.release_id
    assert receipt.release_digest == release.definition_digest
    assert receipt.agent_active is True and receipt.binding_enabled is False
    assert binding.release_id == release.release_id
    assert agent.is_active is True
    again = await service.pin(project, actor, agent.id, workflow_id, command)
    assert again == receipt
    history = await service.history(project, actor, agent.id, limit=20, offset=0)
    assert history.pagination.total == 1
    assert history.data == [receipt]
    assert history.data[0].requested_by == actor
    assert (
        len(
            (
                await db_session.scalars(AgentWorkflowPinTransition.__table__.select())
            ).all()
        )
        == 1
    )

    changed = command.model_copy(update={"release_version": 2})
    with pytest.raises(ConflictError, match="原请求"):
        await service.pin(project, actor, agent.id, workflow_id, changed)
    await db_session.rollback()
    resolver.resolve_published_bindings.assert_awaited_once_with(
        [workflow_id], str(project), {workflow_id: 3}
    )


@pytest.mark.asyncio
async def test_legacy_pin_rejects_cross_project_and_incomplete_release(
    db_session,
    test_project,
):
    project, actor, workflow_id = test_project.id, uuid4(), str(uuid4())
    agent = Agent(
        id=uuid4(),
        project_id=project,
        name="旧员工",
        model="fixture",
        is_active=False,
        is_default=False,
    )
    binding = AgentWorkflow(agent_id=agent.id, workflow_id=workflow_id, enabled=False)
    agent.workflows = [binding]
    db_session.add(agent)
    await db_session.commit()
    agent_id, binding_id = agent.id, binding.id
    owner, (resolver, release) = make_owner(project, actor), make_resolver(
        workflow_id, 2
    )
    service = WorkflowPinTransitionService(db_session, owner=owner, resolver=resolver)
    command = WorkflowPinTransitionCommand(
        request_id=uuid4(),
        expected_binding_id=binding_id,
        release_version=2,
        confirmed_disabled=True,
    )
    foreign_project = uuid4()
    with pytest.raises(ExternalServiceError):
        await service.pin(foreign_project, actor, agent_id, workflow_id, command)
    await db_session.rollback()
    owner.get_agent_configuration_actor.return_value = AgentConfigurationActor(
        project_id=foreign_project, requested_by=actor, checked_at=datetime.now(UTC)
    )
    with pytest.raises(NotFoundError):
        await service.pin(foreign_project, actor, agent_id, workflow_id, command)
    await db_session.rollback()
    owner.get_agent_configuration_actor.return_value = AgentConfigurationActor(
        project_id=project, requested_by=actor, checked_at=datetime.now(UTC)
    )
    resolver.resolve_published_bindings.return_value = {}
    with pytest.raises(ConflictError, match="发布版本"):
        await service.pin(project, actor, agent_id, workflow_id, command)
    await db_session.rollback()
    assert (await db_session.get(AgentWorkflow, binding_id)).release_id is None


def test_legacy_pin_route_requires_private_actor(client, test_project):
    response = client.post(
        f"/api/v1/agents/{uuid4()}/workflows/{uuid4()}/pin-legacy",
        params={"project_id": str(test_project.id)},
        json={
            "request_id": str(uuid4()),
            "expected_binding_id": str(uuid4()),
            "release_version": 2,
            "confirmed_disabled": True,
        },
    )
    assert response.status_code == 403
    history = client.get(
        f"/api/v1/agents/{uuid4()}/workflow-pin-transitions",
        params={"project_id": str(test_project.id)},
    )
    assert history.status_code == 403
