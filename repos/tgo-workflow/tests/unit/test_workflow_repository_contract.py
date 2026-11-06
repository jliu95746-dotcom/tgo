"""Workflow CRUD keeps scope, filtering and independent copies."""

from uuid import uuid4
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects.postgresql import dialect

from app.models.workflow import Workflow
from app.schemas.workflow import WorkflowCreate, WorkflowUpdate
from app.services.workflow_service import WorkflowService
from tests.unit.test_workflow_delete_contract import (
    sessions as _shared_sessions,
)
from tests.unit.test_workflow_releases import definition

sessions = _shared_sessions


@pytest.mark.asyncio
async def test_tag_filters_bind_array_in_both_page_and_count_queries():
    tags = ["quality-tag", "quote'); DROP TABLE wf_workflows; --"]
    page = [object()]
    db = Mock()
    db.execute = AsyncMock(
        side_effect=[
            Mock(scalar_one=Mock(return_value=1)),
            Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=page)))),
        ]
    )
    result, total = await WorkflowService.get_all(db, "project", tags=tags)
    assert result == page and total == 1
    assert db.execute.await_count == 2
    for invocation in db.execute.await_args_list:
        compiled = invocation.args[0].compile(dialect=dialect())
        assert "tags &&" in str(compiled)
        assert tags in compiled.params.values()
        assert tags[1] not in str(compiled)


def request(name, description="synthetic description"):
    graph = definition("synthetic answer")
    for index, node in enumerate(graph["nodes"]):
        node["position"] = {"x": index * 100, "y": 0}
        node["data"].update(type=node["type"], label=node["id"])
    return WorkflowCreate.model_validate({
        "name": name,
        "description": description,
        "tags": [],
        **graph,
    })


@pytest.mark.asyncio
async def test_create_update_and_batch_read_keep_project_and_graph(sessions):
    async with sessions() as db:
        own = await WorkflowService.create(db, "project", request("Original"))
        foreign = await WorkflowService.create(db, "foreign", request("Other"))
        own_id, foreign_id = own.id, foreign.id
        await db.commit()
    async with sessions() as db:
        rows = await WorkflowService.get_by_ids(
            db, "project", [own_id, foreign_id, str(uuid4())]
        )
        assert [row.id for row in rows] == [own_id]
        changed = await WorkflowService.update(
            db,
            own_id,
            "project",
            WorkflowUpdate(
                name="Updated",
                description="updated description",
                nodes=request("Updated").nodes,
                edges=request("Updated").edges,
                expected_version=1,
            ),
        )
        assert changed.id == own_id and changed.version == 2
        await db.commit()
    async with sessions() as db:
        saved = await WorkflowService.get_by_id(db, own_id, "project")
        assert saved.name == "Updated" and saved.version == 2
        assert saved.description == "updated description"
        assert (
            saved.definition["nodes"][1]["data"]["output_template"]
            == "synthetic answer"
        )
        assert (await db.get(Workflow, foreign_id)).name == "Other"


@pytest.mark.asyncio
async def test_list_filters_counts_sorting_and_pagination(sessions):
    async with sessions() as db:
        for name, status, description, project in [
            ("Alpha", "draft", "first", "project"),
            ("Beta", "draft", "alpha in description", "project"),
            ("Gamma", "active", "third", "project"),
            ("Alpha foreign", "draft", "first", "foreign"),
        ]:
            row = await WorkflowService.create(
                db, project, request(name, description)
            )
            row.status = status
        await db.commit()
    async with sessions() as db:
        rows, total = await WorkflowService.get_all(
            db,
            "project",
            status="draft",
            search="alpha",
            tags=[],
            sort_by="name",
            sort_order="asc",
            limit=1,
        )
        assert total == 2 and [row.name for row in rows] == ["Alpha"]
        rows, total = await WorkflowService.get_all(
            db,
            "project",
            status="draft",
            search="alpha",
            sort_by="name",
            sort_order="desc",
            skip=1,
            limit=1,
        )
        assert total == 2 and [row.name for row in rows] == ["Alpha"]
        rows, total = await WorkflowService.get_all(db, "missing-project")
        assert total == 0 and rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize("new_name", [None, "Independent copy"])
async def test_duplicate_has_own_identity_and_draft_version(
    sessions, new_name
):
    async with sessions() as db:
        original = await WorkflowService.create(
            db, "project", request("Original")
        )
        original.version = 5
        original.status = "active"
        await db.commit()
        copied = await WorkflowService.duplicate(
            db, original.id, "project", new_name
        )
        assert copied.id != original.id
        assert copied.name == (new_name or "Original (Copy)")
        assert copied.status == "draft" and copied.version == 1
        assert copied.definition == original.definition
        assert copied.project_id == "project"
        await db.commit()
        assert (
            await WorkflowService.duplicate(db, original.id, "foreign") is None
        )
        assert (
            await WorkflowService.update(
                db, str(uuid4()), "project", WorkflowUpdate(name="Missing")
            )
            is None
        )
