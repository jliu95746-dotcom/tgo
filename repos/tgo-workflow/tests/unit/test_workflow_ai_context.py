"""Only trusted request ownership and a current persisted node reach billing."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.services import workflow_ai_context as authority
from app.services.execution_snapshot import definition_digest


def test_usage_owner_requires_internal_identity_and_complete_pairs(
    monkeypatch,
):
    monkeypatch.setattr(
        authority.settings, "SECRET_KEY", SecretStr("synthetic-scope")
    )
    staff, reservation, lease = str(uuid4()), str(uuid4()), str(uuid4())
    assert authority.request_owner(None, None, None, None, None) is None
    assert (
        authority.request_owner(
            "synthetic-scope", staff, "3", None, None
        ).staff_token_version
        == 3
    )
    assert (
        authority.request_owner(
            "synthetic-scope", None, None, reservation, lease
        ).lease_id
        is not None
    )
    for arguments in [
        (None, staff, "3", None, None),
        ("wrong", staff, "3", None, None),
        ("synthetic-scope", staff, None, None, None),
        ("synthetic-scope", staff, "3", reservation, lease),
    ]:
        with pytest.raises(HTTPException):
            authority.request_owner(*arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", [None, "project", "status", "digest", "node", "owner"]
)
async def test_context_is_bound_to_owned_running_snapshot(
    monkeypatch, invalid
):
    project, execution, agent = uuid4(), uuid4(), uuid4()
    definition = {
        "nodes": [
            {"id": "agent", "type": "agent", "data": {"agent_id": str(agent)}}
        ],
        "edges": [],
    }
    row = SimpleNamespace(
        project_id=str(project),
        id=str(execution),
        status="running",
        definition_snapshot=definition,
        definition_digest=definition_digest(definition),
        workflow_version=1,
        execution_mode="draft_test",
        release_id=None,
        employee_agent_id=None,
        workflow_id="owned-workflow",
        workflow_lineage=["owned-workflow"],
        ai_usage_owner={"staff_id": str(uuid4()), "staff_token_version": 1},
    )
    if invalid == "project":
        row.project_id = str(uuid4())
    if invalid == "status":
        row.status = "cancelled"
    if invalid == "digest":
        row.definition_digest = "0" * 64
    if invalid == "owner":
        row.ai_usage_owner = None
    db = AsyncMock()
    db.scalar.return_value = row
    if invalid in {None, "owner"}:
        context = await authority.read_context(db, project, execution, "agent")
        assert context.agent_id == agent and context.execution_id == execution
        if invalid == "owner":
            assert context.owner.unmetered is True
        else:
            assert context.owner.staff_token_version == 1
    else:
        with pytest.raises(HTTPException):
            await authority.read_context(
                db,
                project,
                execution,
                "unknown" if invalid == "node" else "agent",
            )
