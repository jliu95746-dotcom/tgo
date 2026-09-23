"""Operators configure trial/plans before opening customer subscription enforcement."""

from unittest.mock import Mock
import pytest
import httpx
from fastapi import FastAPI

from app.api.v1.endpoints import operations_billing, operations_tasks
from app.api.v1.endpoints.operations import require_operator
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler


@pytest.mark.asyncio
async def test_prelaunch_operator_can_read_configuration_but_anonymous_cannot(
    monkeypatch,
):
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    monkeypatch.setattr(settings, "OPS_ENABLED", True)
    application = FastAPI()
    application.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    application.include_router(operations_billing.router, prefix="/ops")
    application.include_router(operations_tasks.router, prefix="/ops")
    db = Mock()
    db.scalars.return_value = []
    db.scalar.return_value = None
    application.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        denied = await client.get("/ops/plans")
        assert denied.status_code in {401, 403}
        db.scalars.assert_not_called()
        application.dependency_overrides[require_operator] = lambda: Mock()
        plans = await client.get("/ops/plans")
        policy = await client.get("/ops/trial-policy")
        assert plans.status_code == 200 and plans.json() == []
        assert policy.status_code == 200 and policy.json()["ai_replies"] == 100
    assert not settings.SAAS_BILLING_ENABLED


@pytest.mark.asyncio
async def test_health_requires_operator_before_querying(monkeypatch):
    from datetime import datetime, timezone
    from app.services.commercial_health import CommercialHealthReport

    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    monkeypatch.setattr(settings, "OPS_ENABLED", True)
    report = CommercialHealthReport(
        checked_at=datetime.now(timezone.utc),
        status="attention",
        counts={"paid_unfulfilled": 1},
    )
    scan = Mock(return_value=report)
    monkeypatch.setattr(
        operations_tasks, "inspect_commercial_health", scan, raising=False
    )
    application = FastAPI()
    application.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    application.include_router(operations_tasks.router, prefix="/ops")
    application.dependency_overrides[get_db] = lambda: Mock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        denied = await client.get("/ops/commercial-health")
        assert denied.status_code in {401, 403}
        scan.assert_not_called()
        application.dependency_overrides[require_operator] = lambda: Mock()
        response = await client.get("/ops/commercial-health")
        assert response.status_code == 200
        assert response.json()["counts"]["paid_unfulfilled"] == 1
        assert response.json()["will_change_data"] is False
