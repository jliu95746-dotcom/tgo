"""Malformed saved graphs must not invent cancellation receipts or crash."""

from copy import deepcopy

import pytest
from sqlalchemy import select

from app.models.execution import NodeExecution, WorkflowExecution
from tests.unit.test_parallel_control import (
    cancelled_run,
    record,
    sessions as _shared_sessions,
)
from tests.unit.test_parallel_execution import definition

sessions = _shared_sessions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, value",
    [
        ("nodes", None),
        ("nodes", 9),
        ("nodes", "synthetic-private-receipt-marker"),
        ("edges", None),
        ("edges", []),
        ("edges", "synthetic-private-receipt-marker"),
    ],
)
async def test_malformed_saved_graph_stops_without_receipt(
    sessions, field, value
):
    project, _, run_id = await cancelled_run(sessions)
    graph = deepcopy(definition())
    graph[field].append(value)
    async with sessions() as db:
        run = await db.get(WorkflowExecution, run_id)
        run.definition_snapshot = graph
        await db.commit()
        assert not await record(db, project, run_id)
        assert not (await db.scalars(select(NodeExecution))).all()
        await db.refresh(run)
        assert run.status == "cancelled"
        assert run.input is None and run.output is None
        assert run.definition_snapshot == graph
