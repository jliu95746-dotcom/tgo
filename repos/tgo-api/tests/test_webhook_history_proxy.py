"""Source history keeps the current administrator scope across IO."""

from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import workflow_webhooks as routes
from app.schemas.webhook_history import WebhookDeliveryList
from tests import test_workflow_webhook_proxy as fixtures

harness = fixtures.harness


@pytest.mark.asyncio
async def test_history_injects_current_project_and_preserves_pagination(
    harness, monkeypatch
):
    result = WebhookDeliveryList(
        project_id=harness.project.id,
        workflow_id="synthetic-flow",
        items=[],
        total=0,
        limit=5,
        offset=10,
        has_more=False,
    )
    upstream = AsyncMock(return_value=result)
    monkeypatch.setattr(
        routes.workflow_client, "get_webhook_deliveries", upstream
    )
    response = await harness.client.get(
        "/v1/ai/workflows/synthetic-flow/webhook/deliveries"
        "?limit=5&offset=10&project_id=untrusted",
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    upstream.assert_awaited_once_with(
        "synthetic-flow", harness.project.id, 5, 10
    )


@pytest.mark.asyncio
async def test_history_late_revocation_discards_upstream_result(
    harness, monkeypatch
):
    async def revoke(*args):
        harness.staff.account_enabled = False
        harness.db.commit()
        return WebhookDeliveryList(
            project_id=harness.project.id,
            workflow_id="synthetic-flow",
            items=[],
            total=0,
            limit=10,
            offset=0,
            has_more=False,
        )

    monkeypatch.setattr(
        routes.workflow_client, "get_webhook_deliveries", revoke
    )
    response = await harness.client.get(
        "/v1/ai/workflows/synthetic-flow/webhook/deliveries",
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["limit=101", "offset=-1", "offset=1000001"])
async def test_invalid_history_page_never_calls_workflow(
    harness, monkeypatch, query
):
    upstream = AsyncMock()
    monkeypatch.setattr(
        routes.workflow_client, "get_webhook_deliveries", upstream
    )
    response = await harness.client.get(
        "/v1/ai/workflows/synthetic-flow/webhook/deliveries?" + query,
    )
    assert response.status_code == 422
    upstream.assert_not_called()


@pytest.mark.asyncio
async def test_foreign_history_reply_is_not_forwarded(harness, monkeypatch):
    upstream = AsyncMock(
        return_value=WebhookDeliveryList(
            project_id=harness.project.id,
            workflow_id="foreign-flow",
            items=[],
            total=0,
            limit=10,
            offset=0,
            has_more=False,
        )
    )
    monkeypatch.setattr(
        routes.workflow_client,
        "get_webhook_deliveries",
        upstream,
    )
    response = await harness.client.get(
        "/v1/ai/workflows/synthetic-flow/webhook/deliveries",
    )
    assert response.status_code == 502
