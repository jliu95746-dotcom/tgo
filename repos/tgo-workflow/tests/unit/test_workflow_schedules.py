"""Persistent timer configuration and original-command receipts."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import JsonValue
import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database import Base
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_schedule import (
    WorkflowSchedule,
    WorkflowScheduleCommand,
)
from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence
from app.models.execution import WorkflowExecution, NodeExecution
from app.models.approval import WorkflowApprovalRequest
from app.schemas.workflow_schedule import ScheduleDisable, ScheduleSave
from app.services.execution_snapshot import definition_digest
from app.services import workflow_schedules as schedules


@dataclass
class Fixture:
    sessions: async_sessionmaker[AsyncSession]
    project_id: UUID
    actor_id: UUID
    workflow_id: str
    release_id: str
    digest: str


def definition(expression: str = "0 9 * * 1-5") -> dict[str, JsonValue]:
    return {
        "nodes": [
            {
                "id": "clock",
                "type": "timer",
                "data": {
                    "type": "timer",
                    "label": "定时整理",
                    "reference_key": "clock",
                    "cron_expression": expression,
                    "timezone": "Asia/Shanghai",
                },
            },
            {
                "id": "answer",
                "type": "answer",
                "data": {
                    "reference_key": "answer",
                    "output_type": "template",
                    "output_template": "模拟定时结果",
                },
            },
        ],
        "edges": [{"source": "clock", "target": "answer"}],
    }


@asynccontextmanager
async def schedule_database() -> AsyncIterator[Fixture]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn,
                tables=[
                    Workflow.__table__,
                    WorkflowRelease.__table__,
                    WorkflowSchedule.__table__,
                    WorkflowScheduleCommand.__table__,
                    WorkflowTimerOccurrence.__table__,
                    WorkflowExecution.__table__,
                    NodeExecution.__table__,
                    WorkflowApprovalRequest.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, actor_id = uuid4(), uuid4()
    workflow_id, release_id = str(uuid4()), str(uuid4())
    graph = definition()
    digest = definition_digest(graph)
    async with sessions() as db:
        db.add(
            Workflow(
                id=workflow_id,
                project_id=str(project_id),
                name="合成定时测试",
                definition=graph,
                status="active",
                version=1,
                published_version=1,
                publication_revision=1,
            )
        )
        await db.flush()
        db.add(
            WorkflowRelease(
                id=release_id,
                workflow_id=workflow_id,
                project_id=str(project_id),
                version=1,
                draft_version=1,
                definition=graph,
                definition_digest=digest,
            )
        )
        await db.commit()
    try:
        yield Fixture(
            sessions, project_id, actor_id, workflow_id, release_id, digest
        )
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def fixture() -> AsyncIterator[Fixture]:
    async with schedule_database() as result:
        yield result


def request(
    fixture: Fixture, *, revision: int = 0, enabled: bool = False
) -> ScheduleSave:
    return ScheduleSave(
        request_id=uuid4(),
        expected_revision=revision,
        enabled=enabled,
        expected_release_id=fixture.release_id,
        expected_release_digest=fixture.digest,
    )


@pytest.mark.asyncio
async def test_disabled_plan_keeps_the_published_rule_and_has_no_due_work(
    fixture: Fixture,
) -> None:
    async with fixture.sessions() as db:
        receipt = await schedules.save_schedule(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            request(fixture),
        )
        assert receipt.schedule.revision == 1
        assert receipt.schedule.enabled is False
        assert receipt.schedule.next_run_at is None
        assert receipt.schedule.cron_expression == "0 9 * * 1-5"
        assert receipt.schedule.timezone == "Asia/Shanghai"
        assert receipt.schedule.release_id == fixture.release_id
        assert receipt.replayed is False
        view = await schedules.get_schedule(
            db, fixture.workflow_id, fixture.project_id
        )
        assert view.binding_current is True
        assert view.execution_available is False
        assert len(view.preview_at) == 3
        assert all(value.tzinfo is timezone.utc for value in view.preview_at)


@pytest.mark.asyncio
async def test_lost_reply_recovers_original_snapshot_after_later_changes(
    fixture: Fixture,
) -> None:
    first_request = request(fixture)
    async with fixture.sessions() as db:
        first = await schedules.save_schedule(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            first_request,
        )
        await schedules.disable_schedule(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            ScheduleDisable(request_id=uuid4(), expected_revision=1),
        )
        await db.execute(update(Workflow).values(status="draft"))
        await db.commit()
        recovered = await schedules.save_schedule(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            first_request,
        )
        assert recovered.schedule == first.schedule
        assert recovered.replayed is True
        assert (
            await schedules.get_schedule(
                db, fixture.workflow_id, fixture.project_id
            )
        ).schedule.revision == 2
        receipt = await schedules.get_command_receipt(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            first_request.request_id,
        )
        assert receipt.schedule == first.schedule
        assert (
            await db.scalar(select(func.count()).select_from(WorkflowSchedule))
            == 1
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowScheduleCommand)
            )
            == 2
        )


@pytest.mark.asyncio
async def test_same_request_number_with_different_content_or_actor_conflicts(
    fixture: Fixture,
) -> None:
    original = request(fixture)
    async with fixture.sessions() as db:
        await schedules.save_schedule(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            original,
        )
        for changed, actor in [
            (
                original.model_copy(update={"expected_revision": 1}),
                fixture.actor_id,
            ),
            (original, uuid4()),
        ]:
            with pytest.raises(HTTPException) as denied:
                await schedules.save_schedule(
                    db, fixture.workflow_id, fixture.project_id, actor, changed
                )
            assert denied.value.status_code == 409


@pytest.mark.asyncio
async def test_stale_revision_cannot_overwrite_or_create_a_command(
    fixture: Fixture,
) -> None:
    async with fixture.sessions() as db:
        await schedules.save_schedule(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            request(fixture),
        )
        with pytest.raises(HTTPException) as denied:
            await schedules.save_schedule(
                db,
                fixture.workflow_id,
                fixture.project_id,
                fixture.actor_id,
                request(fixture),
            )
        assert denied.value.status_code == 409
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowScheduleCommand)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_wrong_project_cannot_read_save_disable_or_recover(
    fixture: Fixture,
) -> None:
    original = request(fixture)
    async with fixture.sessions() as db:
        await schedules.save_schedule(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            original,
        )
        for operation in (
            schedules.get_schedule(db, fixture.workflow_id, uuid4()),
            schedules.save_schedule(
                db,
                fixture.workflow_id,
                uuid4(),
                fixture.actor_id,
                request(fixture),
            ),
            schedules.disable_schedule(
                db,
                fixture.workflow_id,
                uuid4(),
                fixture.actor_id,
                ScheduleDisable(request_id=uuid4(), expected_revision=1),
            ),
            schedules.get_command_receipt(
                db,
                fixture.workflow_id,
                uuid4(),
                fixture.actor_id,
                original.request_id,
            ),
        ):
            with pytest.raises(HTTPException) as denied:
                await operation
            assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_enable_is_rejected_without_saving_when_dispatch_is_unavailable(
    fixture: Fixture,
) -> None:
    async with fixture.sessions() as db:
        with pytest.raises(HTTPException) as denied:
            await schedules.save_schedule(
                db,
                fixture.workflow_id,
                fixture.project_id,
                fixture.actor_id,
                request(fixture, enabled=True),
            )
        assert denied.value.status_code == 409
        assert (
            await db.scalar(select(func.count()).select_from(WorkflowSchedule))
            == 0
        )


@pytest.mark.asyncio
async def test_changed_release_must_be_rebound_but_stop_remains_available(
    fixture: Fixture,
) -> None:
    async with fixture.sessions() as db:
        await schedules.save_schedule(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            request(fixture),
        )
        await db.execute(update(Workflow).values(published_version=2))
        await db.commit()
        view = await schedules.get_schedule(
            db, fixture.workflow_id, fixture.project_id
        )
        assert view.binding_current is False
        with pytest.raises(HTTPException):
            await schedules.save_schedule(
                db,
                fixture.workflow_id,
                fixture.project_id,
                fixture.actor_id,
                request(fixture, revision=1),
            )
        stopped = await schedules.disable_schedule(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            ScheduleDisable(request_id=uuid4(), expected_revision=1),
        )
        assert stopped.schedule.revision == 2
        assert stopped.schedule.next_run_at is None


@pytest.mark.asyncio
async def test_saved_release_proof_must_match_its_definition(
    fixture: Fixture,
) -> None:
    async with fixture.sessions() as db:
        await db.execute(
            update(WorkflowRelease).values(definition_digest="0" * 64)
        )
        await db.commit()
        with pytest.raises(HTTPException) as denied:
            await schedules.save_schedule(
                db,
                fixture.workflow_id,
                fixture.project_id,
                fixture.actor_id,
                request(fixture),
            )
        assert denied.value.status_code == 409
        assert (
            await db.scalar(select(func.count()).select_from(WorkflowSchedule))
            == 0
        )


@pytest.mark.asyncio
async def test_committed_reply_is_detached_from_expiring_orm_state(
    fixture: Fixture,
) -> None:
    fixture.sessions.configure(expire_on_commit=True)
    async with fixture.sessions() as db:
        result = await schedules.save_schedule(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            request(fixture),
        )
        assert result.schedule.revision == 1
        assert result.schedule.enabled is False


@pytest.mark.asyncio
async def test_invalid_published_timer_returns_a_bounded_conflict(
    fixture: Fixture,
) -> None:
    graph = definition()
    nodes = graph["nodes"]
    assert isinstance(nodes, list) and isinstance(nodes[0], dict)
    data = nodes[0]["data"]
    assert isinstance(data, dict)
    data["type"] = "event"
    fixture.digest = definition_digest(graph)
    async with fixture.sessions() as db:
        await db.execute(
            update(WorkflowRelease).values(
                definition=graph, definition_digest=fixture.digest
            )
        )
        await db.commit()
        with pytest.raises(HTTPException) as denied:
            await schedules.save_schedule(
                db,
                fixture.workflow_id,
                fixture.project_id,
                fixture.actor_id,
                request(fixture),
            )
        assert denied.value.status_code == 409
        assert (
            await db.scalar(select(func.count()).select_from(WorkflowSchedule))
            == 0
        )
