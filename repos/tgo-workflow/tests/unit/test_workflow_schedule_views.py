"""Preview and history stay tenant scoped and never invent successful runs."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.execution import WorkflowExecution
from app.models.workflow_timer_occurrence import WorkflowTimerOccurrence
from app.services.timer_state import execution_id
from tests.unit.test_timer_dispatch import due_plan
from tests.unit import test_workflow_schedules_http as shared_http

client = shared_http.client
fixture = shared_http.fixture

HEADERS = {"X-Internal-API-Key": "synthetic-schedule-key"}


@pytest.mark.asyncio
async def test_preview_uses_server_time_without_creating_plan(client, fixture):
    root = "/v1/workflows/" + fixture.workflow_id + "/schedule"
    response = await client.post(
        root + "/preview",
        headers=HEADERS,
        params={"project_id": str(fixture.project_id)},
        json={"cron_expression": "0 9 * * 1-5", "timezone": "Asia/Shanghai"},
    )
    assert response.status_code == 200
    body = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert len(body["preview_at"]) == 5
    computed = datetime.fromisoformat(body["computed_at"])
    times = [datetime.fromisoformat(value) for value in body["preview_at"]]
    assert times == sorted(set(times)) and min(times) > computed
    async with fixture.sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowTimerOccurrence)
            )
            == 0
        )
    state = await client.get(
        root,
        headers=HEADERS,
        params={"project_id": str(fixture.project_id)},
    )
    context = state.json()["current_release"]
    assert context["release_id"] == fixture.release_id
    assert context["definition_digest"] == fixture.digest
    assert state.json()["schedule"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"cron_expression": "0 0 30 2 *", "timezone": "UTC"},
        {"cron_expression": "0 9 * * *", "timezone": "Private/Unknown"},
        {"cron_expression": "0 9 * * *", "timezone": True},
        {"cron_expression": "0 9 * * *", "project_id": "foreign"},
    ],
)
async def test_preview_rejects_invalid_or_identity_override(
    client, fixture, body
):
    response = await client.post(
        f"/v1/workflows/{fixture.workflow_id}/schedule/preview",
        headers=HEADERS,
        params={"project_id": str(fixture.project_id)},
        json=body,
    )
    assert response.status_code in {400, 422}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix, method",
    [
        ("/preview", "POST"),
        ("/occurrences", "GET"),
    ],
)
async def test_views_require_internal_key_and_tenant(
    client, fixture, suffix, method
):
    path = f"/v1/workflows/{fixture.workflow_id}/schedule" + suffix
    options = (
        {"json": {"cron_expression": "* * * * *"}} if method == "POST" else {}
    )
    response = await client.request(
        method,
        path,
        params={"project_id": str(fixture.project_id)},
        **options,
    )
    assert response.status_code == 403
    response = await client.request(
        method,
        path,
        headers=HEADERS,
        params={"project_id": str(uuid4())},
        **options,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("channel", ["scheduled_timer", "internal_event"])
async def test_history_separates_queue_and_run_and_excludes_foreign_run(
    client, fixture, foreign, channel
):
    now = datetime.now(timezone.utc)
    plan = await due_plan(fixture, now)
    source_id = str(uuid4())
    run_id = execution_id(source_id)
    async with fixture.sessions() as db:
        db.add(
            WorkflowExecution(
                id=run_id,
                workflow_id=fixture.workflow_id,
                project_id=str(uuid4())
                if foreign
                else str(fixture.project_id),
                status="completed",
                source_channel=channel,
                execution_mode="production",
                release_id=fixture.release_id,
                definition_digest=fixture.digest,
                source_message_id="timer-" + source_id,
                input={"private": "omit"},
            )
        )
        await db.flush()
        db.add(
            WorkflowTimerOccurrence(
                id=source_id,
                project_id=str(fixture.project_id),
                workflow_id=fixture.workflow_id,
                schedule_id=plan,
                revision=1,
                scheduled_at=now,
                actor_staff_id=str(fixture.actor_id),
                release_id=fixture.release_id,
                release_digest=fixture.digest,
                status="queued",
                execution_id=run_id,
                attempts=1,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    response = await client.get(
        f"/v1/workflows/{fixture.workflow_id}/schedule/occurrences",
        headers=HEADERS,
        params={"project_id": str(fixture.project_id), "limit": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1 and body["has_more"] is False
    row = body["items"][0]
    assert row["status"] == "queued"
    trusted = not foreign and channel == "scheduled_timer"
    assert row["execution_id"] == (run_id if trusted else None)
    assert row["execution_status"] == ("completed" if trusted else None)
    assert "private" not in response.text and "actor_staff_id" not in row


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [{"limit": 51}, {"offset": -1}])
async def test_history_pagination_is_bounded(client, fixture, query):
    response = await client.get(
        f"/v1/workflows/{fixture.workflow_id}/schedule/occurrences",
        headers=HEADERS,
        params={"project_id": str(fixture.project_id), **query},
    )
    assert response.status_code == 422
