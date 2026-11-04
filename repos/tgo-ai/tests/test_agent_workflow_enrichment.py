"""The employee editor must retain local workflow bindings during remote outages."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.agent import Agent
from app.models.workflow import AgentWorkflow
from app.schemas.agent import AgentWithDetails
from app.services.agent_service import AgentService
from app.services.workflow_service import WorkflowData


def employee_with_binding(project_id):
    employee = Agent(
        id=uuid4(),
        project_id=project_id,
        name="订单员工",
        model="fixture",
        is_active=False,
        is_default=False,
    )
    workflow_id = str(uuid4())
    employee.workflows = [
        AgentWorkflow(
            agent_id=employee.id,
            workflow_id=workflow_id,
            enabled=True,
            release_version=2,
            release_id=str(uuid4()),
            release_digest="a" * 64,
        )
    ]
    return employee, workflow_id


@pytest.mark.asyncio
async def test_outage_keeps_employee_binding_and_pinned_release(
    monkeypatch,
    db_session,
    test_project,
):
    employee, workflow_id = employee_with_binding(test_project.id)
    db_session.add(employee)
    await db_session.flush()
    from app.services import agent_service

    monkeypatch.setattr(
        agent_service.workflow_service_client,
        "get_workflows_batch",
        AsyncMock(side_effect=ConnectionError("workflow unavailable")),
    )

    await AgentService(db_session).enrich_agents_with_workflow_data(
        [employee],
        test_project.id,
    )
    response = AgentWithDetails.model_validate(employee)

    assert len(response.workflows) == 1
    assert response.workflows[0].id == workflow_id
    assert response.workflows[0].details_available is False
    assert response.workflows[0].updated_at is None
    assert response.workflows[0].bound_release_version == 2
    assert response.workflows[0].bound_release_id == employee.workflows[0].release_id


@pytest.mark.asyncio
async def test_partial_remote_result_keeps_missing_local_binding(
    monkeypatch,
    db_session,
    test_project,
):
    employee, missing_id = employee_with_binding(test_project.id)
    present_id = str(uuid4())
    employee.workflows.append(
        AgentWorkflow(
            agent_id=employee.id,
            workflow_id=present_id,
            enabled=True,
            release_version=3,
            release_id=str(uuid4()),
            release_digest="b" * 64,
        )
    )
    db_session.add(employee)
    await db_session.flush()
    from app.services import agent_service

    monkeypatch.setattr(
        agent_service.workflow_service_client,
        "get_workflows_batch",
        AsyncMock(
            return_value=[
                WorkflowData(
                    id=present_id,
                    name="物流查询",
                    status="active",
                    version=4,
                    updated_at="2026-10-14T00:00:00Z",
                )
            ]
        ),
    )

    await AgentService(db_session).enrich_agents_with_workflow_data(
        [employee],
        test_project.id,
    )
    response = AgentWithDetails.model_validate(employee)

    by_id = {item.id: item for item in response.workflows}
    assert set(by_id) == {missing_id, present_id}
    assert by_id[missing_id].details_available is False
    assert by_id[present_id].details_available is True
    assert by_id[missing_id].bound_release_version == 2
    assert by_id[present_id].bound_release_version == 3


@pytest.mark.asyncio
async def test_removed_binding_is_not_returned_as_an_unavailable_workflow(
    monkeypatch,
    db_session,
    test_project,
):
    employee, _ = employee_with_binding(test_project.id)
    employee.workflows[0].deleted_at = datetime.now(timezone.utc)
    db_session.add(employee)
    await db_session.flush()
    from app.services import agent_service

    read_remote = AsyncMock()
    monkeypatch.setattr(
        agent_service.workflow_service_client,
        "get_workflows_batch",
        read_remote,
    )

    await AgentService(db_session).enrich_agents_with_workflow_data(
        [employee],
        test_project.id,
    )

    assert AgentWithDetails.model_validate(employee).workflows == []
    read_remote.assert_not_awaited()
