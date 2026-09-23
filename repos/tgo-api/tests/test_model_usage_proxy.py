"""Model accounting reads use internal HTTP and preserve unknown costs."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.core.config import settings
from app.services import model_usage


@pytest.mark.asyncio
async def test_proxy_forwards_project_and_preserves_unknown_cost(monkeypatch):
    project = uuid4()
    record = {
        "id": str(uuid4()),
        "project_id": str(project),
        "reservation_id": None,
        "model_name": "synthetic",
        "purpose": "standard",
        "status": "failed",
        "input_tokens": None,
        "output_tokens": None,
        "estimated_cost_fen": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
    }
    monkeypatch.setattr(
        settings, "SAAS_INTERNAL_TOKEN", SecretStr("synthetic-internal-test-token")
    )
    get = AsyncMock(
        return_value=httpx.Response(
            200,
            json=[record],
            request=httpx.Request("GET", "https://synthetic.invalid"),
        )
    )
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    result = await model_usage.list_model_usage(project, 50, 50)
    assert result[0].estimated_cost_fen is None
    assert get.call_args.kwargs["params"]["project_id"] == str(project)
    assert get.call_args.kwargs["headers"] == {
        "X-SaaS-Service-Token": "synthetic-internal-test-token"
    }


@pytest.mark.asyncio
async def test_unavailable_service_does_not_fake_empty_cost_report(monkeypatch):
    monkeypatch.setattr(
        settings, "SAAS_INTERNAL_TOKEN", SecretStr("synthetic-internal-test-token")
    )
    monkeypatch.setattr(
        httpx.AsyncClient, "get", AsyncMock(side_effect=httpx.ConnectError("synthetic"))
    )
    with pytest.raises(HTTPException) as error:
        await model_usage.list_model_usage(None, 0, 50)
    assert error.value.status_code == 503
