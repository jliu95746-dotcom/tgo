"""Timer views retain administrator identity before and after upstream wait."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from app.api.v1.endpoints import workflow_schedules as routes
from app.schemas.workflow_schedule_views import (
    ScheduleOccurrenceList,
    SchedulePreview,
)
from tests import test_workflow_schedule_proxy as shared_proxy

harness = shared_proxy.harness


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, suffix, client_method",
    [
        ("POST", "preview", "preview_schedule"),
        ("GET", "occurrences", "list_schedule_occurrences"),
    ],
)
async def test_timer_views_fix_tenant_and_recheck_after_wait(
    harness,
    monkeypatch,
    method,
    suffix,
    client_method,
):
    now = datetime.now(timezone.utc)
    result = (
        SchedulePreview(
            cron_expression="0 9 * * *",
            timezone="UTC",
            computed_at=now,
            preview_at=[],
        )
        if method == "POST"
        else ScheduleOccurrenceList(
            items=[],
            total=0,
            limit=10,
            offset=0,
            has_more=False,
        )
    )
    call = AsyncMock(return_value=result)
    monkeypatch.setattr(routes.workflow_client, client_method, call)
    options = (
        {"json": {"cron_expression": "0 9 * * *"}} if method == "POST" else {}
    )
    url = "/v1/ai/workflows/owned/schedule/" + suffix
    response = await harness.client.request(method, url, **options)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert call.await_args.args[:2] == ("owned", harness.project.id)

    async def revoke(*args):
        harness.staff.role = "user"
        harness.db.commit()
        return result

    call.side_effect = revoke
    response = await harness.client.request(method, url, **options)
    assert response.status_code == 403
    call.reset_mock()
    response = await harness.client.request(method, url, **options)
    assert response.status_code == 403
    call.assert_not_awaited()
