"""Persisted stop signals interrupt in-flight branches and keep safe receipts."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.engine.executor import WorkflowExecutor
from app.engine.executor import WorkflowNodeFailure
from app.engine.nodes.llm import LLMNodeExecutor
from app.models.execution import NodeExecution, WorkflowExecution
from app.models.workflow import Workflow
from app.services.execution_lifecycle import ExecutionStopped
from tests.unit.test_parallel_execution import definition


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [ExecutionStopped("stopped"), RuntimeError("private authority")]
)
async def test_control_failure_interrupts_two_blocking_calls_without_join(monkeypatch, failure):
    started = set()
    cancelled = set()
    receipts = []
    control_calls = 0

    async def execute(self, context):
        started.add(self.node_id)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.add(self.node_id)
            raise

    async def check_active():
        nonlocal control_calls
        control_calls += 1
        if len(started) == 2:
            raise failure

    async def complete(**receipt):
        receipts.append(receipt)

    monkeypatch.setattr(LLMNodeExecutor, "execute", execute)
    with pytest.raises(type(failure)) as result:
        await asyncio.wait_for(
            WorkflowExecutor(
                definition(),
                check_active=check_active,
            ).run({}, on_node_complete=complete),
            1.5,
        )
    assert result.value is failure
    assert cancelled == {"first", "second"}
    assert not any(row["node_id"] == "join" for row in receipts)
    assert {row["node_id"] for row in receipts if row["status"] == "cancelled"} == cancelled
    before = control_calls
    await asyncio.sleep(0.3)
    assert control_calls == before


@pytest.mark.asyncio
async def test_monitor_shares_callback_lock_and_leaves_no_background_checks(monkeypatch):
    active = 0
    maximum = 0
    checks = 0

    async def callback(**receipt):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1

    async def check_active():
        nonlocal checks
        checks += 1
        await callback()

    async def execute(self, context):
        await asyncio.sleep(0.4)
        return {"text": self.node_id}, None

    monkeypatch.setattr(LLMNodeExecutor, "execute", execute)
    assert (
        await WorkflowExecutor(definition(), check_active=check_active).run(
            {},
            on_node_start=callback,
            on_node_complete=callback,
        )
        == "first + second"
    )
    assert maximum == 1 and checks >= 1
    before = checks
    await asyncio.sleep(0.3)
    assert checks == before


@pytest.mark.asyncio
async def test_branch_failure_does_not_cancel_shared_database_check_mid_transaction(monkeypatch):
    checking = asyncio.Event()
    check_cancelled = False

    async def check_active():
        nonlocal check_cancelled
        checking.set()
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            check_cancelled = True
            raise

    async def execute(self, context):
        await checking.wait()
        if self.node_id == "first":
            raise ValueError("synthetic branch failure")
        await asyncio.Event().wait()

    monkeypatch.setattr(LLMNodeExecutor, "execute", execute)
    with pytest.raises(WorkflowNodeFailure):
        await WorkflowExecutor(definition(), check_active=check_active).run({})
    assert not check_cancelled


@pytest.fixture
async def sessions(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "parallel.sqlite"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def cancelled_run(sessions, *, status="cancelled"):
    graph = definition()
    project, flow_id, run_id = (str(uuid4()) for _ in range(3))
    async with sessions() as db:
        db.add(Workflow(id=flow_id, project_id=project, name="synthetic", definition=graph))
        db.add(
            WorkflowExecution(
                id=run_id,
                workflow_id=flow_id,
                project_id=project,
                status=status,
                input=None,
                output=None,
                definition_snapshot=graph,
            )
        )
        await db.commit()
    return project, flow_id, run_id


async def record(db, project, run_id, **changes):
    from app.services.parallel_node_receipts import preserve_cancelled_parallel_node

    ended = datetime.now(timezone.utc)
    return await preserve_cancelled_parallel_node(
        db,
        **{
            "project_id": project,
            "execution_id": run_id,
            "node_id": "first",
            "node_type": "llm",
            "started_at": ended - timedelta(milliseconds=25),
            "completed_at": ended,
            "duration": 25,
            **changes,
        }
    )


@pytest.mark.asyncio
async def test_cancelled_branch_metadata_is_durable_private_and_idempotent(sessions):
    project, _, run_id = await cancelled_run(sessions)
    async with sessions() as db:
        assert await record(db, project, run_id)
        assert await record(db, project, run_id)
        rows = (await db.scalars(select(NodeExecution))).all()
        assert len(rows) == 1
        assert rows[0].status == "cancelled"
        assert rows[0].input == {"capture_status": "redacted"}
        assert rows[0].output is None and rows[0].duration == 25
        assert rows[0].completed_at > rows[0].started_at
        run = await db.get(WorkflowExecution, run_id)
        assert run.status == "cancelled" and run.output is None and run.input is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "failed", "completed", "pending"])
async def test_cancelled_receipt_does_not_mutate_other_run_states(sessions, status):
    project, _, run_id = await cancelled_run(sessions, status=status)
    async with sessions() as db:
        assert not await record(db, project, run_id)
        assert not (await db.scalars(select(NodeExecution))).all()
        assert (await db.get(WorkflowExecution, run_id)).status == status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"project_id": "another-project"},
        {"node_id": "join", "node_type": "answer"},
        {"node_id": "missing"},
        {"node_type": "api"},
        {"duration": -1},
    ],
)
async def test_cancelled_receipt_rejects_wrong_scope_nodes_and_invalid_times(sessions, changes):
    project, _, run_id = await cancelled_run(sessions)
    async with sessions() as db:
        assert not await record(db, project, run_id, **changes)
        assert not (await db.scalars(select(NodeExecution))).all()
