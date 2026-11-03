"""A project can inspect old unpinned bindings without changing live employees."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.agent import Agent
from app.models.project import Project
from app.models.workflow import AgentWorkflow
from app.services.workflow_release_audit import list_unpinned_workflows
from app.services.workflow_service import WorkflowData


def test_workflow_release_audit_route_is_registered(client, test_project):
    path = "/api/v1/agents/workflow-release-audit"
    response = client.get(path, params={"project_id": str(test_project.id)})
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0
    assert (
        client.get(
            path, params={"project_id": str(test_project.id), "limit": 101}
        ).status_code
        == 422
    )


def employee(
    project_id,
    name: str,
    workflow_id: str,
    *,
    pinned: bool = False,
    active: bool = False,
):
    agent = Agent(
        id=uuid4(),
        project_id=project_id,
        name=name,
        model="fixture",
        is_active=active,
        is_default=False,
    )
    agent.workflows = [
        AgentWorkflow(
            agent_id=agent.id,
            workflow_id=workflow_id,
            enabled=True,
            release_version=2 if pinned else None,
            release_id=str(uuid4()) if pinned else None,
            release_digest="a" * 64 if pinned else None,
        )
    ]
    return agent


@pytest.mark.asyncio
async def test_audit_is_project_scoped_paginated_and_never_pins(
    db_session,
    test_project,
):
    other_project = Project(
        id=uuid4(),
        name="Other Project",
        api_key="ak_other_project",
    )
    workflow_id = str(uuid4())
    unpinned = employee(test_project.id, "待处理员工", workflow_id, active=True)
    pinned = employee(test_project.id, "已固定员工", str(uuid4()), pinned=True)
    foreign = employee(other_project.id, "其他企业员工", str(uuid4()))
    removed = employee(test_project.id, "已移除员工", str(uuid4()))
    removed.workflows[0].deleted_at = datetime.now(timezone.utc)
    db_session.add_all([other_project, unpinned, pinned, foreign, removed])
    await db_session.commit()
    remote = AsyncMock(
        return_value=[
            WorkflowData(
                id=workflow_id,
                name="商品咨询",
                status="active",
                version=5,
                published_version=3,
                updated_at="2026-10-14T00:00:00Z",
            )
        ]
    )

    empty = await list_unpinned_workflows(
        db_session,
        test_project.id,
        limit=1,
        offset=1,
        read_workflows=remote,
    )
    result = await list_unpinned_workflows(
        db_session,
        test_project.id,
        limit=1,
        offset=0,
        read_workflows=remote,
    )

    assert empty.pagination.total == 1 and empty.data == []
    assert result.pagination.total == 1 and result.pagination.has_next is False
    assert result.data[0].agent_id == unpinned.id
    assert result.data[0].binding_id == unpinned.workflows[0].id
    assert result.data[0].workflow_id == workflow_id
    assert result.data[0].current_published_version == 3
    assert result.data[0].workflow_name == "商品咨询"
    assert result.data[0].agent_active is True
    remote.assert_awaited_once_with([workflow_id], str(test_project.id))
    assert unpinned.workflows[0].release_version is None
    assert unpinned.workflows[0].release_id is None


@pytest.mark.asyncio
async def test_audit_retains_local_items_when_workflow_owner_is_down(
    db_session,
    test_project,
):
    workflow_id = str(uuid4())
    agent = employee(test_project.id, "待处理员工", workflow_id)
    db_session.add(agent)
    await db_session.commit()

    result = await list_unpinned_workflows(
        db_session,
        test_project.id,
        limit=20,
        offset=0,
        read_workflows=AsyncMock(side_effect=ConnectionError("unavailable")),
    )

    assert result.pagination.total == 1
    assert result.data[0].workflow_id == workflow_id
    assert result.data[0].current_published_version is None
    assert result.data[0].details_available is False
