"""Keep the existing knowledge-catalog identity boundary strict."""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import agent_knowledge_cases as catalogs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        "missing_owner",
        "null_owner",
        "wrong_owner",
        "invalid_owner",
        "missing_id",
        "null_id",
        "wrong_id",
    ],
)
async def test_catalog_rejects_unproven_owner_before_database(
    invalid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, actor, agent = uuid4(), uuid4(), uuid4()
    payload: dict[str, object] = {"id": str(agent), "project_id": str(project)}
    if invalid == "missing_owner":
        payload.pop("project_id")
    elif invalid == "null_owner":
        payload["project_id"] = None
    elif invalid == "wrong_owner":
        payload["project_id"] = str(uuid4())
    elif invalid == "invalid_owner":
        payload["project_id"] = "invalid-id"
    elif invalid == "missing_id":
        payload.pop("id")
    elif invalid == "null_id":
        payload["id"] = None
    else:
        payload["id"] = str(uuid4())
    remote = AsyncMock(return_value=payload)
    database = Mock(side_effect=AssertionError("Unproven identity reached DB"))
    monkeypatch.setattr(catalogs.ai_client, "get_agent", remote)
    monkeypatch.setattr(catalogs, "_read", database)
    with pytest.raises(HTTPException) as error:
        await catalogs.read(project, actor, agent)
    assert error.value.status_code == 404
    database.assert_not_called()
    remote.assert_awaited_once_with(
        str(project), str(agent), include_tools=False
    )


@pytest.mark.asyncio
async def test_verified_employee_keeps_original_actor_and_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, actor, agent = uuid4(), uuid4(), uuid4()
    catalog = catalogs.response(agent, None)
    remote = AsyncMock(
        return_value={"id": str(agent), "project_id": str(project)}
    )
    database = Mock(return_value=catalog)
    monkeypatch.setattr(catalogs.ai_client, "get_agent", remote)
    monkeypatch.setattr(catalogs, "_read", database)
    result = await catalogs.read(project, actor, agent)
    assert result is catalog
    database.assert_called_once_with(project, actor, agent)
