import pytest
from unittest.mock import AsyncMock
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ValidationError as SchemaValidationError

from app.schemas.agent import AgentCreate, AgentUpdate
from app.services.agent_service import AgentService
from app.models.agent import Agent
from app.models.workflow import AgentWorkflow
from app.exceptions import ConflictError, ValidationError
from app.services.workflow_service import WorkflowIntegrationRelease


@pytest.mark.asyncio
async def test_device_action_readiness_requires_the_agents_fixed_tested_policy(
    db_session: AsyncSession, test_project, monkeypatch,
) -> None:
    from app.services import agent_service as module
    from app.schemas.device_access import BoundDeviceReadPolicy

    device_id = uuid4()
    agent = Agent(
        project_id=test_project.id, name="设备员工", model="fixture",
        bound_device_id=str(device_id), bound_device_policy_digest="a" * 64,
    )
    db_session.add(agent)
    await db_session.commit()
    policy = BoundDeviceReadPolicy(
        project_id=test_project.id, device_id=device_id, tool_name="fs_read",
        status="active", verified=True, digest="a" * 64,
    )
    check = AsyncMock(return_value=policy)
    monkeypatch.setattr(module.device_control_client, "validate_binding", check)
    service = AgentService(db_session)

    ready = await service.device_action_readiness(test_project.id, agent.id)
    assert ready.status == "ready"
    assert ready.device_id == str(device_id)
    assert ready.policy_digest == "a" * 64
    check.assert_awaited_with(test_project.id, str(device_id))

    check.return_value = policy.model_copy(update={"digest": "b" * 64})
    stale = await service.device_action_readiness(test_project.id, agent.id)
    assert stale.status == "needs_rebind"
    assert stale.policy_digest is None

    check.return_value = policy.model_copy(update={"device_id": uuid4()})
    wrong_device = await service.device_action_readiness(test_project.id, agent.id)
    assert wrong_device.status == "needs_rebind"

    check.return_value = None
    legacy = await service.device_action_readiness(test_project.id, agent.id)
    assert legacy.status == "legacy_unverified"


@pytest.mark.asyncio
async def test_device_action_readiness_fails_closed_for_unverified_policy(
    db_session: AsyncSession, test_project, monkeypatch,
) -> None:
    from app.services import agent_service as module

    device_id = uuid4()
    agent = Agent(
        project_id=test_project.id, name="待测设备员工", model="fixture",
        bound_device_id=str(device_id), bound_device_policy_digest="a" * 64,
    )
    db_session.add(agent)
    await db_session.commit()
    check = AsyncMock(side_effect=ValidationError("设备测试失效", "bound_device_id"))
    monkeypatch.setattr(module.device_control_client, "validate_binding", check)
    result = await AgentService(db_session).device_action_readiness(
        test_project.id, agent.id,
    )
    assert result.status == "needs_test"


@pytest.mark.asyncio
async def test_device_action_readiness_does_not_check_unbound_agent(
    db_session: AsyncSession, test_project, monkeypatch,
) -> None:
    from app.services import agent_service as module

    agent = Agent(project_id=test_project.id, name="普通员工", model="fixture")
    db_session.add(agent)
    await db_session.commit()
    check = AsyncMock()
    monkeypatch.setattr(module.device_control_client, "validate_binding", check)
    result = await AgentService(db_session).device_action_readiness(
        test_project.id, agent.id,
    )
    assert result.status == "not_bound"
    check.assert_not_awaited()


def test_device_action_readiness_api_has_typed_response(
    client, test_project, monkeypatch,
) -> None:
    from app.config import settings
    from app.dependencies import get_agent_service
    from app.main import app
    from app.schemas.device_access import AgentDeviceActionReadiness

    agent_id = uuid4()
    service = type("AgentReadinessService", (), {})()
    service.device_action_readiness = AsyncMock(return_value=AgentDeviceActionReadiness(
        agent_id=agent_id, status="not_bound",
    ))
    monkeypatch.setitem(app.dependency_overrides, get_agent_service, lambda: service)
    monkeypatch.setattr(settings, "secret_key", "synthetic-readiness-key")
    path = f"/api/v1/agents/{agent_id}/device-action-readiness"
    assert client.get(path, params={"project_id": str(test_project.id)}).status_code == 403
    service.device_action_readiness.assert_not_awaited()
    headers = {
        "X-Internal-API-Key": "synthetic-readiness-key",
        "X-Project-Id": str(test_project.id),
        "X-Staff-Id": str(uuid4()),
    }
    assert client.get(path, params={"project_id": str(uuid4())}, headers=headers).status_code == 403
    response = client.get(path, params={"project_id": str(test_project.id)}, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "not_bound"
    service.device_action_readiness.assert_awaited_once_with(
        test_project.id, agent_id,
    )


@pytest.mark.asyncio
async def test_device_reference_listing_filters_before_pagination(
    db_session: AsyncSession, test_project,
) -> None:
    device_id, other_device_id = uuid4(), uuid4()
    db_session.add_all([
        Agent(project_id=test_project.id, name="订单员工", model="fixture",
              bound_device_id=str(device_id)),
        Agent(project_id=test_project.id, name="售后员工", model="fixture",
              bound_device_id=str(device_id)),
        Agent(project_id=test_project.id, name="其他设备员工", model="fixture",
              bound_device_id=str(other_device_id)),
    ])
    await db_session.commit()

    agents, total = await AgentService(db_session).list_agents(
        test_project.id, bound_device_id=device_id, limit=1, offset=0,
        enrich_resources=False,
    )
    assert total == 2
    assert len(agents) == 1
    assert agents[0].bound_device_id == str(device_id)


@pytest.mark.parametrize("version", [True, 0, -1, "2"])
def test_employee_release_selection_requires_a_positive_integer(version) -> None:
    with pytest.raises(SchemaValidationError):
        AgentUpdate(workflow_release_versions={str(uuid4()): version})


@pytest.mark.asyncio
async def test_create_agent_cannot_replace_existing_default_without_testing(
    db_session: AsyncSession,
    test_project,
) -> None:
    service = AgentService(db_session)

    first = Agent(project_id=test_project.id, name="First", model="openai:gpt-4o",
                  is_default=True, is_active=True)
    db_session.add(first)
    await db_session.commit()
    with pytest.raises(ConflictError):
        await service.create_agent(test_project.id, AgentCreate(name="Second", model="openai:gpt-4o", is_default=True))

    refreshed_first = await service.get_agent(test_project.id, first.id)
    assert refreshed_first.is_default is True and refreshed_first.is_active


@pytest.mark.asyncio
async def test_get_default_agent_returns_project_default(
    db_session: AsyncSession,
    test_project,
) -> None:
    service = AgentService(db_session)
    created = Agent(id=uuid4(), project_id=test_project.id, name="Default", model="openai:gpt-4o",
                    is_default=True, is_active=True)
    db_session.add(created)
    await db_session.commit()

    default_agent = await service.get_default_agent(test_project.id)

    assert default_agent.id == created.id


@pytest.mark.asyncio
async def test_workflow_binding_pins_published_release_on_save(
    db_session: AsyncSession, test_project, monkeypatch
) -> None:
    from app.services import agent_service as module

    workflow_id = str(uuid4())
    resolved = WorkflowIntegrationRelease(
        workflow_id=workflow_id,
        release_version=2,
        release_id=str(uuid4()),
        definition_digest="a" * 64,
        input_parameters=[],
    )
    resolve = AsyncMock(return_value={workflow_id: resolved})
    monkeypatch.setattr(
        module.workflow_service_client, "resolve_published_bindings", resolve
    )
    service = AgentService(db_session)
    created = await service.create_agent(
        test_project.id,
        AgentCreate(name="订单员工", model="fixture", workflows=[workflow_id]),
    )
    binding = created.workflows[0]
    assert binding.release_version == 2
    assert binding.release_id == resolved.release_id
    assert binding.release_digest == "a" * 64
    resolve.assert_awaited_once_with([workflow_id], str(test_project.id))

    resolve.reset_mock()
    renamed = await service.update_agent(
        test_project.id, created.id,
        AgentUpdate(name="新昵称", workflows=[workflow_id]),
        enrich_resources=False,
    )
    assert renamed.workflows[0].release_version == 2
    resolve.assert_not_awaited()

    newer = resolved.model_copy(update={"release_version": 3, "definition_digest": "b" * 64})
    resolve.return_value = {workflow_id: newer}
    refreshed = await service.update_agent(
        test_project.id, created.id,
        AgentUpdate(workflows=[workflow_id], refresh_workflow_releases=True),
        enrich_resources=False,
    )
    assert refreshed.workflows[0].release_version == 3
    assert refreshed.workflows[0].release_digest == "b" * 64
    resolve.assert_awaited_once_with([workflow_id], str(test_project.id))


@pytest.mark.asyncio
async def test_employee_can_choose_a_published_workflow_version(
    db_session: AsyncSession, test_project, monkeypatch
) -> None:
    from app.services import agent_service as module

    workflow_id = str(uuid4())
    historical = WorkflowIntegrationRelease(
        workflow_id=workflow_id, release_version=1, release_id=str(uuid4()),
        definition_digest="a" * 64, input_parameters=[],
    )
    resolve = AsyncMock(return_value={workflow_id: historical})
    monkeypatch.setattr(
        module.workflow_service_client, "resolve_published_bindings", resolve
    )
    service = AgentService(db_session)
    created = await service.create_agent(
        test_project.id,
        AgentCreate(
            name="历史版员工", model="fixture", workflows=[workflow_id],
            workflow_release_versions={workflow_id: 1},
        ),
    )
    assert created.workflows[0].release_version == 1
    resolve.assert_awaited_once_with(
        [workflow_id], str(test_project.id), {workflow_id: 1}
    )

    current = historical.model_copy(update={
        "release_version": 2, "release_id": str(uuid4()),
        "definition_digest": "b" * 64,
    })
    resolve.reset_mock()
    resolve.return_value = {workflow_id: current}
    changed = await service.update_agent(
        test_project.id, created.id,
        AgentUpdate(workflow_release_versions={workflow_id: 2}),
        enrich_resources=False,
    )
    assert changed.workflows[0].release_version == 2
    assert changed.workflows[0].release_id == current.release_id
    resolve.assert_awaited_once_with(
        [workflow_id], str(test_project.id), {workflow_id: 2}
    )

    with pytest.raises(ValidationError, match="工作流"):
        await service.update_agent(
            test_project.id, created.id,
            AgentUpdate(workflow_release_versions={str(uuid4()): 1}),
            enrich_resources=False,
        )


@pytest.mark.asyncio
async def test_legacy_workflow_cannot_be_pinned_by_general_editor(
    db_session: AsyncSession, test_project, monkeypatch,
) -> None:
    from app.services import agent_service as module

    workflow_id = str(uuid4())
    agent = Agent(project_id=test_project.id, name="旧员工", model="fixture",
                  is_active=False)
    agent.workflows = [AgentWorkflow(workflow_id=workflow_id, enabled=False)]
    db_session.add(agent)
    await db_session.commit()
    agent_id = agent.id
    project_id = test_project.id
    resolve = AsyncMock(return_value={workflow_id: WorkflowIntegrationRelease(
        workflow_id=workflow_id, release_version=2, release_id=str(uuid4()),
        definition_digest="a" * 64,
    )})
    monkeypatch.setattr(module.workflow_service_client,
                        "resolve_published_bindings", resolve)
    service = AgentService(db_session)

    for payload in (
        AgentUpdate(workflow_release_versions={workflow_id: 2}),
        AgentUpdate(refresh_workflow_releases=True),
    ):
        with pytest.raises(ConflictError, match="旧关联"):
            await service.update_agent(project_id, agent_id, payload,
                                       enrich_resources=False)
        await db_session.rollback()
    resolve.assert_not_awaited()

    with pytest.raises(ConflictError, match="固定发布版本"):
        await service.set_workflow_enabled(project_id, agent_id,
                                           workflow_id, True)
    assert (await service.get_agent(project_id, agent_id,
                                    enrich_resources=False)).workflows[0].release_version is None
