"""Require owned knowledge evidence before creating or editing an agent."""

from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select

from app.exceptions import NotFoundError, ValidationError
from app.models.agent import Agent
from app.models.collection import AgentCollection
from app.schemas.agent import AgentCreate, AgentUpdate
from app.services import agent_service as module
from app.services.agent_service import AgentService
from tests.test_resource_batch_clients import (
    COLLECTION,
    OTHER,
    collection,
    http_reply as resource_http_reply,
)

http_reply = resource_http_reply


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize(
    "problem", ["missing", "unaccounted", "foreign", "transport"]
)
async def test_failed_lookup_cannot_save_or_replace_collection_bindings(
    db_session, test_project, monkeypatch, http_reply, operation, problem
):
    service = AgentService(db_session)
    original = Agent(
        project_id=test_project.id,
        name="原有员工",
        model="fixture",
        is_active=False,
        is_default=False,
        skills_enabled=False,
    )
    db_session.add(original)
    await db_session.flush()
    db_session.add(AgentCollection(agent_id=original.id, collection_id=OTHER))
    await db_session.commit()
    monkeypatch.setattr(
        module.rag_service_client, "base_url", "https://lookup.example.test"
    )
    body = {"collections": [], "not_found": [COLLECTION]}
    if problem == "unaccounted":
        body["not_found"] = []
    elif problem == "foreign":
        body = {"collections": [collection(OTHER)], "not_found": []}
    requests = http_reply(
        body, error=httpx.ConnectError if problem == "transport" else None
    )

    with pytest.raises((ValidationError, NotFoundError)):
        if operation == "create":
            await service.create_agent(
                test_project.id,
                AgentCreate(
                    name="新员工", model="fixture", collections=[COLLECTION]
                ),
            )
        else:
            await service.update_agent(
                test_project.id,
                original.id,
                AgentUpdate(name="不应保存的新名称", collections=[COLLECTION]),
                enrich_resources=False,
            )

    await db_session.refresh(original)
    assert original.name == "原有员工" and not original.is_active
    assert await db_session.scalar(select(func.count(Agent.id))) == 1
    bindings = (await db_session.scalars(select(AgentCollection))).all()
    assert len(bindings) == 1 and bindings[0].collection_id == OTHER
    assert bindings[0].agent_id == original.id
    assert len(requests) == 1
    assert requests[0].url.params["project_id"] == str(test_project.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_current_collection_can_be_saved_without_enabling_the_employee(
    db_session, test_project, monkeypatch, http_reply, operation
):
    monkeypatch.setattr(
        module.rag_service_client, "base_url", "https://lookup.example.test"
    )
    requests = http_reply({"collections": [collection()], "not_found": []})
    service = AgentService(db_session)
    if operation == "create":
        saved = await service.create_agent(
            test_project.id,
            AgentCreate(
                name="产品客服", model="fixture", collections=[COLLECTION]
            ),
        )
    else:
        original = Agent(
            project_id=test_project.id,
            name="产品客服",
            model="fixture",
            is_active=False,
            is_default=False,
            skills_enabled=False,
        )
        db_session.add(original)
        await db_session.commit()
        saved = await service.update_agent(
            test_project.id,
            original.id,
            AgentUpdate(collections=[COLLECTION]),
            enrich_resources=False,
        )
    assert not saved.is_active and not saved.is_default
    bindings = (await db_session.scalars(select(AgentCollection))).all()
    assert len(bindings) == 1 and UUID(bindings[0].collection_id) == UUID(
        COLLECTION
    )
    assert bindings[0].agent_id == saved.id
    assert len(requests) == 1
