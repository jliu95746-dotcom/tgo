"""Availability checks the registered protocol and actor before and after HTTP."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.core.config import settings
from app.schemas.agent_business_execution import (
    BusinessRuntimeQuery,
    BusinessRuntimeCapabilities,
)
from app.services import business_runtime_readiness as service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing_token",
        "missing_field",
        "missing_customer_operation",
        "unknown_protocol",
        "stale",
        "oversized",
        "down",
        "revoked_actor",
    ],
)
async def test_private_runtime_observation_is_current_complete_and_owned(
    monkeypatch, fault
):
    query = BusinessRuntimeQuery(project_id=uuid4(), requested_by=uuid4())
    permission = Mock(
        side_effect=[None, HTTPException(403, "当前管理员已撤销")]
        if fault == "revoked_actor"
        else None
    )
    monkeypatch.setattr(service, "current_configuration_actor", permission)
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_SERVICE_TOKEN",
        None
        if fault == "missing_token"
        else SecretStr("fixture-service-key-at-least-32-characters"),
    )
    body = BusinessRuntimeCapabilities(
        operations=["order_query", "logistics_query", "customer_profile_query", "membership_query"],
        checked_at=datetime.now(UTC)
    ).model_dump(mode="json")
    if fault == "missing_field":
        body.pop("can_enable")
    elif fault == "missing_customer_operation":
        body["operations"].remove("membership_query")
    elif fault == "unknown_protocol":
        body["protocol_version"] = 3
    elif fault == "stale":
        body["checked_at"] = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    requests = []

    def respond(request):
        requests.append(request)
        if fault == "oversized":
            return httpx.Response(200, content=b"x" * 5000)
        return httpx.Response(503 if fault == "down" else 200, json=body)

    if fault == "revoked_actor":
        with pytest.raises(HTTPException):
            await service.current_business_runtime(
                Mock(), query, transport=httpx.MockTransport(respond)
            )
    else:
        result = await service.current_business_runtime(
            Mock(), query, transport=httpx.MockTransport(respond)
        )
        assert result.available is (fault is None) and result.can_enable is False
        assert (
            result.project_id == query.project_id
            and result.requested_by == query.requested_by
        )
    assert permission.call_count == 2
    assert len(requests) == (0 if fault == "missing_token" else 1)
