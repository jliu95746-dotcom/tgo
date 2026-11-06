"""Malformed employee metadata cannot mark setup ready or permit removal."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import employee_style, onboarding_service, provider_usage


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", ["one", {"id": "one"}, [None], [7]])
async def test_onboarding_does_not_count_invalid_employee_records(
    monkeypatch, rows
):
    lookup = AsyncMock(return_value={"data": rows})
    monkeypatch.setattr(
        onboarding_service.AIServiceClient, "list_agents", lookup
    )
    assert (
        await onboarding_service.check_step_4_agent_created(uuid4()) is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows,expected", [([], False), ([{"id": "one"}], True)]
)
async def test_onboarding_counts_normal_employee_records(
    monkeypatch, rows, expected
):
    lookup = AsyncMock(return_value={"data": rows})
    monkeypatch.setattr(
        onboarding_service.AIServiceClient, "list_agents", lookup
    )
    assert (
        await onboarding_service.check_step_4_agent_created(uuid4())
        is expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, 7, [], {}])
async def test_employee_style_uses_only_text_names(monkeypatch, value):
    client = SimpleNamespace(
        get_agent=AsyncMock(
            return_value={
                "id": "employee",
                "is_active": True,
                "name": value,
                "humanization_skill_name": value,
                "humanization_skill_enabled": True,
            }
        )
    )
    monkeypatch.setattr(employee_style, "ai_client", client)
    result = await employee_style.resolve_employee_style("project", "employee")
    assert result.agent_name is None and result.skill_name is None
    assert result.enabled is False and result.agent_id == "employee"


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[None], [7], {"items": []}])
async def test_provider_removal_is_blocked_for_invalid_reference_rows(
    monkeypatch, rows
):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(
        provider_usage.ai_client,
        "list_agents",
        AsyncMock(
            return_value={"data": rows},
        ),
    )
    with pytest.raises(HTTPException) as error:
        await provider_usage.assert_provider_unused(db, uuid4(), uuid4())
    assert error.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value", [("llm_provider_id", []), ("model", [])]
)
async def test_provider_removal_is_blocked_when_reference_identity_is_invalid(
    monkeypatch,
    field,
    value,
):
    provider = uuid4()
    row = {"llm_provider_id": str(provider), "model": "used"}
    row[field] = value
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(
        provider_usage.ai_client,
        "list_agents",
        AsyncMock(
            return_value={"data": [row]},
        ),
    )
    with pytest.raises(HTTPException) as error:
        await provider_usage.assert_provider_unused(
            db, uuid4(), provider, {"used"}
        )
    assert error.value.status_code == 503
