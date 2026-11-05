"""Reception configuration preserves defaults and explicit enablement."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx
import pytest
import pytest_asyncio
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.api.v1.endpoints import visitor_assignment_rules as endpoint
from app.core import security
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import http_exception_handler
from app.models import AIProvider, Staff, VisitorAssignmentRule


PRIVATE_USERNAME = "synthetic-private-reception-username"
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def reception(monkeypatch):
    staff = Staff(
        id=uuid4(),
        project_id=uuid4(),
        username=PRIVATE_USERNAME,
        password_hash="disabled-fixture-password",
        role="admin",
    )
    db = MagicMock(spec=Session)
    state = {"rule": None, "provider": None, "allowed": True}
    rule_query = MagicMock()
    provider_query = MagicMock()
    rule_query.filter.return_value.first.side_effect = lambda: state["rule"]
    provider_query.filter.return_value.first.side_effect = lambda: state[
        "provider"
    ]
    db.query.side_effect = lambda model: (
        rule_query if model is VisitorAssignmentRule else provider_query
    )

    def add(rule):
        state["rule"] = rule

    def refresh(rule):
        rule.id = rule.id or uuid4()
        rule.created_at = rule.created_at or datetime(2026, 1, 1)
        rule.updated_at = rule.updated_at or datetime(2026, 1, 1)

    db.add.side_effect = add
    db.refresh.side_effect = refresh
    monkeypatch.setattr(
        security, "get_current_user", AsyncMock(return_value=staff)
    )
    monkeypatch.setattr(
        security,
        "check_user_permission",
        lambda *_args, **_kwargs: state["allowed"],
    )
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    monkeypatch.setattr(
        settings, "ASSIGNMENT_RULE_DEFAULT_WEEKDAYS", "1,2,3,4,5,6,7"
    )
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/rules")
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        client.headers["Authorization"] = "Bearer synthetic-reception-session"
        yield client, db, state, staff, rule_query, provider_query


def stored_rule(staff, *, enabled=True):
    return VisitorAssignmentRule(
        id=uuid4(),
        project_id=staff.project_id,
        ai_provider_id=None,
        model="custom-model",
        prompt="custom prompt",
        llm_assignment_enabled=enabled,
        timezone="Asia/Shanghai",
        service_weekdays=[1, 3, 5],
        service_start_time="09:00",
        service_end_time="18:00",
        max_concurrent_chats=12,
        auto_close_hours=48,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


async def test_saved_configuration_reads_without_side_effects(reception):
    client, db, state, staff, *_rest = reception
    rule = stored_rule(staff, enabled=False)
    state["rule"] = rule
    response = await client.get("/rules")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(rule.id)
    assert body["prompt"] == "custom prompt"
    assert body["effective_prompt"] == "custom prompt"
    assert body["service_weekdays"] == [1, 3, 5]
    assert body["llm_assignment_enabled"] is False
    db.add.assert_not_called()
    db.commit.assert_not_called()


async def test_default_prompt_is_available_to_authorized_staff(reception):
    client, db, *_rest = reception
    response = await client.get("/rules/default-prompt")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "default_prompt": endpoint.DEFAULT_ASSIGNMENT_PROMPT
    }
    db.query.assert_not_called()


@pytest.mark.parametrize(
    "path, method",
    [("/rules", "get"), ("/rules", "put"), ("/rules/default-prompt", "get")],
)
async def test_rules_require_staff_authentication(reception, path, method):
    client, db, *_rest = reception
    client.headers.pop("Authorization")
    response = await client.request(
        method, path, **({"json": {}} if method == "put" else {})
    )
    assert response.status_code == 403
    db.query.assert_not_called()
    db.commit.assert_not_called()


async def test_fresh_project_can_read_defaults_without_creating_a_rule(
    reception,
):
    client, db, _state, staff, *_queries = reception
    response = await client.get("/rules")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == str(staff.project_id)
    assert body["service_weekdays"] == [1, 2, 3, 4, 5, 6, 7]
    assert body["llm_assignment_enabled"] is False
    assert (
        body["effective_prompt"] == endpoint.DEFAULT_ASSIGNMENT_PROMPT.strip()
    )
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("method", ["get", "put"])
async def test_default_weekdays_share_validated_ordering(
    reception, monkeypatch, method
):
    client, _db, _state, *_rest = reception
    monkeypatch.setattr(
        settings, "ASSIGNMENT_RULE_DEFAULT_WEEKDAYS", " 5, 1, 5, 3 "
    )
    response = (
        await client.get("/rules")
        if method == "get"
        else await client.put("/rules", json={})
    )
    assert response.status_code == 200, response.text
    assert response.json()["service_weekdays"] == [1, 3, 5]


@pytest.mark.parametrize("payload", [{}, {"max_concurrent_chats": 20}])
async def test_first_save_does_not_implicitly_enable_assignment(
    reception, payload
):
    client, db, state, *_rest = reception
    response = await client.put("/rules", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["llm_assignment_enabled"] is False
    assert state["rule"].llm_assignment_enabled is False
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(state["rule"])


@pytest.mark.parametrize("initial", [None, False, True])
@pytest.mark.parametrize("requested", [False, True])
async def test_enablement_changes_only_when_explicitly_requested(
    reception, initial, requested
):
    client, _db, state, staff, *_rest = reception
    if initial is not None:
        state["rule"] = stored_rule(staff, enabled=initial)
    response = await client.put(
        "/rules", json={"llm_assignment_enabled": requested}
    )
    assert response.status_code == 200, response.text
    assert response.json()["llm_assignment_enabled"] is requested
    assert state["rule"].llm_assignment_enabled is requested


@pytest.mark.parametrize("initial", [False, True])
async def test_partial_updates_preserve_existing_assignment_choice(
    reception, initial
):
    client, _db, state, staff, *_rest = reception
    state["rule"] = stored_rule(staff, enabled=initial)
    response = await client.put("/rules", json={"max_concurrent_chats": 19})
    assert response.status_code == 200, response.text
    assert response.json()["llm_assignment_enabled"] is initial
    assert response.json()["service_weekdays"] == [1, 3, 5]
    assert response.json()["max_concurrent_chats"] == 19


@pytest.mark.parametrize("existing", [False, True])
async def test_null_enablement_is_rejected_before_any_mutation(
    reception, existing
):
    client, db, state, staff, *_rest = reception
    if existing:
        state["rule"] = stored_rule(staff)
    response = await client.put(
        "/rules", json={"llm_assignment_enabled": None, "prompt": "new"}
    )
    assert response.status_code == 422, response.text
    if existing:
        assert state["rule"].prompt == "custom prompt"
        assert state["rule"].llm_assignment_enabled is True
    else:
        assert state["rule"] is None
    db.add.assert_not_called()
    db.commit.assert_not_called()


async def test_nullable_configuration_can_still_be_cleared(reception):
    client, _db, state, staff, *_rest = reception
    state["rule"] = stored_rule(staff)
    response = await client.put(
        "/rules", json={"prompt": None, "model": None, "ai_provider_id": None}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["prompt"] is None and body["model"] is None
    assert body["ai_provider_id"] is None
    assert (
        body["effective_prompt"] == endpoint.DEFAULT_ASSIGNMENT_PROMPT.strip()
    )


async def test_provider_lookup_is_active_and_scoped_to_current_project(
    reception,
):
    client, db, state, staff, _rule_query, provider_query = reception
    rule = stored_rule(staff)
    state["rule"] = rule
    provider_id = uuid4()
    response = await client.put(
        "/rules", json={"ai_provider_id": str(provider_id), "prompt": "new"}
    )
    assert response.status_code == 404, response.text
    clauses = provider_query.filter.call_args.args
    expected = (
        AIProvider.id == provider_id,
        AIProvider.project_id == staff.project_id,
        AIProvider.deleted_at.is_(None),
    )
    assert len(clauses) == len(expected)
    assert all(
        actual.compare(wanted) for actual, wanted in zip(clauses, expected)
    )
    assert rule.prompt == "custom prompt"
    db.commit.assert_not_called()


async def test_valid_provider_choice_persists(reception):
    client, _db, state, staff, *_rest = reception
    provider = AIProvider(id=uuid4(), project_id=staff.project_id)
    state["provider"] = provider
    response = await client.put(
        "/rules", json={"ai_provider_id": str(provider.id)}
    )
    assert response.status_code == 200, response.text
    assert response.json()["ai_provider_id"] == str(provider.id)
    assert response.json()["llm_assignment_enabled"] is False


@pytest.mark.parametrize("method", ["get", "put"])
async def test_rule_lookup_uses_authenticated_project(reception, method):
    client, _db, _state, staff, query, _provider_query = reception
    response = (
        await client.get("/rules?project_id=" + str(uuid4()))
        if method == "get"
        else await client.put("/rules", json={"project_id": str(uuid4())})
    )
    assert response.status_code == 200, response.text
    assert response.json()["project_id"] == str(staff.project_id)
    (clause,) = query.filter.call_args.args
    assert clause.compare(VisitorAssignmentRule.project_id == staff.project_id)
    compiled = clause.compile(dialect=postgresql.dialect())
    assert list(compiled.params.values()) == [staff.project_id]


@pytest.mark.parametrize(
    "payload",
    [
        {"service_weekdays": [0]},
        {"service_weekdays": [8]},
        {"service_start_time": "25:00"},
        {"max_concurrent_chats": 0},
    ],
)
async def test_invalid_configuration_never_writes(reception, payload):
    client, db, *_rest = reception
    response = await client.put("/rules", json=payload)
    assert response.status_code == 422
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "path, method",
    [("/rules", "get"), ("/rules", "put"), ("/rules/default-prompt", "get")],
)
async def test_permissions_are_checked_before_rule_access(
    reception, path, method
):
    client, db, state, *_rest = reception
    state["allowed"] = False
    response = await client.request(
        method, path, json={} if method == "put" else None
    )
    assert response.status_code == 403
    db.query.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("method", ["get", "put"])
async def test_configuration_access_does_not_log_private_username(
    reception, caplog, method
):
    client, *_rest = reception
    with caplog.at_level("INFO"):
        response = await client.request(
            method, "/rules", **({"json": {}} if method == "put" else {})
        )
    assert response.status_code == 200, response.text
    records = [
        record
        for record in caplog.records
        if record.name.startswith(
            (
                "endpoints.visitor_assignment_rules",
                "services.visitor_assignment_rule",
            )
        )
    ]
    assert PRIVATE_USERNAME not in repr([vars(record) for record in records])
