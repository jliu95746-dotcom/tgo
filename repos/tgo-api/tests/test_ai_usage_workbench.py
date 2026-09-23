"""Workbench retries replay the original generation without another model call."""

from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.schemas.chat import AssistDraftResponse
from app.services import ai_usage_runtime, ai_usage_workbench
from tests.test_ai_usage import prepare
from tests.test_billing_quotes import commercial_company  # noqa: F401


@pytest.mark.asyncio
async def test_workbench_retry_replays_one_charge(commercial_company, monkeypatch):
    db, staff, batch = prepare(commercial_company)

    @contextmanager
    def session():
        yield db

    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    monkeypatch.setattr(ai_usage_workbench, "SessionLocal", session)
    monkeypatch.setattr(ai_usage_runtime, "SessionLocal", session)
    monkeypatch.setattr(ai_usage_workbench.asyncio, "to_thread", immediate)
    generate = AsyncMock(return_value=AssistDraftResponse(draft="已查询到物流记录。"))
    first = await ai_usage_workbench.generate_workbench(staff.project_id, "assist:test", "same", generate, AssistDraftResponse)
    second = await ai_usage_workbench.generate_workbench(staff.project_id, "assist:test", "same", generate, AssistDraftResponse)
    assert first == second
    assert generate.await_count == 1 and batch.remaining == 0
    with pytest.raises(TGOAPIException) as error:
        await ai_usage_workbench.generate_workbench(staff.project_id, "assist:test", "changed", generate, AssistDraftResponse)
    assert error.value.code == "AI_REQUEST_CONFLICT"


@pytest.mark.asyncio
async def test_workbench_failed_generation_releases_quota(commercial_company, monkeypatch):
    db, staff, batch = prepare(commercial_company)

    @contextmanager
    def session():
        yield db

    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    monkeypatch.setattr(ai_usage_workbench, "SessionLocal", session)
    monkeypatch.setattr(ai_usage_runtime, "SessionLocal", session)
    monkeypatch.setattr(ai_usage_workbench.asyncio, "to_thread", immediate)
    generate = AsyncMock(side_effect=RuntimeError("synthetic model failure"))
    with pytest.raises(RuntimeError):
        await ai_usage_workbench.generate_workbench(staff.project_id, "assist:failed", "same", generate, AssistDraftResponse)
    assert batch.remaining == 1
