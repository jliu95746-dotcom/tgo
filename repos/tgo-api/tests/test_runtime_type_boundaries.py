"""Keep public request compatibility and defensive runtime fallbacks."""

import asyncio
from datetime import datetime, timedelta, timezone
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, ValidationError
import pytest

from app.api.internal.endpoints.users import parse_user_id
from app.api.v1.endpoints import onboarding, setup
from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models import Staff
from app.services import humanization_service, platform_sync
from tests.conftest import SyncASGIClient


class RequestInput(BaseModel):
    value: int


def test_openapi_subclass_preserves_unified_validation_and_response_cache():
    application_class = import_module("app.api.openapi").TGOApplication
    application = application_class()

    @application.post("/records")
    def create_record(body: RequestInput) -> dict[str, int]:
        return {"value": body.value}

    schema = application.openapi()
    assert schema is application.openapi()
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    assert "ValidationError" not in schema["components"]["schemas"]
    assert "ErrorResponse" in schema["components"]["schemas"]
    assert schema["paths"]["/records"]["post"]["responses"]["422"] == {
        "description": "Validation Error", "content": {
            "application/json": {"schema": {
                "$ref": "#/components/schemas/ErrorResponse",
            }},
        },
    }


def test_openapi_cache_only_rebuilds_after_explicit_invalidation():
    application = import_module("app.api.openapi").TGOApplication()
    before = application.openapi()

    @application.get("/later")
    def later() -> str:
        return "ready"

    assert application.openapi() is before
    assert "/later" not in before["paths"]
    application.openapi_schema = None
    after = application.openapi()
    assert after is not before and "/later" in after["paths"]


def test_application_factory_keeps_extension_routers_and_settings_metadata():
    from app.core.config import settings
    from app.main import create_app

    application_class = import_module("app.api.openapi").TGOApplication
    router = APIRouter()

    @router.get("/extension-type-contract")
    def extra() -> str:
        return "extension"

    application = create_app(additional_routers=[router])
    assert isinstance(application, application_class)
    schema = application.openapi()
    assert schema["info"]["title"] == settings.PROJECT_NAME
    assert schema["info"]["version"] == settings.PROJECT_VERSION
    assert settings.API_V1_STR + "/extension-type-contract" in schema["paths"]


@pytest.fixture
def onboarding_api(monkeypatch):
    project_id = uuid4()
    project = SimpleNamespace(id=project_id)
    progress = SimpleNamespace(
        id=uuid4(), project_id=project_id, is_completed=False,
        completed_at=None, created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
        **{f"step_{i}_completed": False for i in range(1, 6)},
    )
    query = SimpleNamespace()
    query.filter = lambda *_: query
    query.first = lambda: project
    db = SimpleNamespace(query=lambda _: query, commit=Mock(), refresh=Mock())

    async def check_steps(*_):
        flags = [getattr(progress, f"step_{i}_completed") for i in range(1, 6)]
        return flags, 1, sum(flags)

    monkeypatch.setattr(onboarding, "_get_or_create_progress",
                        lambda *_: progress)
    monkeypatch.setattr(onboarding, "check_all_steps", check_steps)
    application = FastAPI()
    application.include_router(onboarding.router, prefix="/onboarding")
    application.dependency_overrides[get_db] = lambda: db
    application.dependency_overrides[get_current_active_user] = lambda: Staff(
        id=uuid4(), project_id=project_id, username="test-staff", role="admin",
    )
    with_client = SyncASGIClient(application)
    try:
        yield with_client, progress, db, application
    finally:
        with_client.close()


@pytest.mark.parametrize("body", [None, "null", "{}", '{"step_number":null}'])
def test_skip_onboarding_keeps_missing_null_and_empty_body_behavior(
    onboarding_api, body,
):
    client, progress, db, _ = onboarding_api
    response = client.post(
        "/onboarding/skip", content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert progress.is_completed
    assert all(getattr(progress, f"step_{i}_completed") for i in range(1, 6))
    assert response.json()["progress_percentage"] == 100
    db.commit.assert_called()


def test_skip_one_onboarding_step_keeps_other_steps(onboarding_api):
    client, progress, _, _ = onboarding_api
    response = client.post("/onboarding/skip", json={"step_number": 3})
    assert response.status_code == 200
    assert [getattr(progress, f"step_{i}_completed") for i in range(1, 6)] == [
        False, False, True, False, False,
    ]
    assert not progress.is_completed
    assert response.json()["progress_percentage"] == 25


@pytest.mark.parametrize("value", [0, 6, "invalid"])
def test_skip_onboarding_rejects_invalid_steps_before_commit(
    onboarding_api, value,
):
    client, progress, db, _ = onboarding_api
    response = client.post("/onboarding/skip", json={"step_number": value})
    assert response.status_code == 422
    assert not progress.is_completed
    db.commit.assert_not_called()


def test_onboarding_schema_documents_the_existing_null_body(onboarding_api):
    _, _, _, application = onboarding_api
    schema = application.openapi()["paths"]["/onboarding/skip"]["post"]
    body = schema["requestBody"]["content"]["application/json"]["schema"]
    assert {"type": "null"} in body.get("anyOf", [])
    assert schema["requestBody"].get("required", False) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_saved_date", [False, True])
async def test_setup_completion_keeps_required_date_validation(
    monkeypatch, invalid_saved_date,
):
    state = SimpleNamespace(
        is_installed=False, admin_created=True, llm_configured=False,
        skip_llm_config=False, setup_completed_at=None,
    )
    monkeypatch.setattr(setup, "_get_or_create_system_setup", lambda _: state)

    def refresh(row):
        if invalid_saved_date:
            row.setup_completed_at = None

    db = SimpleNamespace(commit=Mock(), refresh=refresh)
    request = SimpleNamespace(url=SimpleNamespace(path="/v1/setup/skip-llm"))
    if invalid_saved_date:
        with pytest.raises(ValidationError):
            await setup.skip_llm_configuration(request=request, db=db)
    else:
        response = await setup.skip_llm_configuration(request=request, db=db)
        assert response.is_installed
        assert isinstance(response.setup_completed_at, datetime)
        assert response.setup_completed_at == state.setup_completed_at


@pytest.mark.parametrize("value, expected", [
    ("abc-vtr", ("visitor", "abc")),
    ("abc-staff", ("staff", "abc")),
    ("legacy", ("visitor", "legacy")),
    (42, ("visitor", "42")),
    (None, ("visitor", "None")),
])
def test_internal_user_parser_keeps_non_string_compatibility(value, expected):
    assert parse_user_id(value) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_timestamp", [False, True])
async def test_platform_retry_retains_missing_timestamp_fallback(
    monkeypatch, missing_timestamp,
):
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    platform = SimpleNamespace(
        id=uuid4(), sync_retry_count=0, last_synced_at=None, updated_at=None,
        created_at=None if missing_timestamp else now - timedelta(minutes=1),
    )
    query = SimpleNamespace()
    query.filter = lambda *_: query
    query.limit = lambda *_: query
    query.all = lambda: [platform]
    db = SimpleNamespace(query=lambda _: query, close=Mock())
    monkeypatch.setattr(platform_sync, "SessionLocal", lambda: db)
    monkeypatch.setattr(platform_sync, "_now", lambda: now)
    trigger = Mock()
    monkeypatch.setattr(platform_sync, "trigger_platform_sync", trigger)
    monkeypatch.setattr(platform_sync.settings,
                        "PLATFORM_SYNC_RETRY_INTERVAL_SECONDS", 10)
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(platform_sync.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await platform_sync._retry_loop()
    assert trigger.call_count == int(not missing_timestamp)
    db.close.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_payload", [None, [], "raw", 42])
async def test_recent_history_keeps_skipping_malformed_legacy_records(
    monkeypatch, invalid_payload,
):
    from app.services import customer_history
    from app.services.wukongim_client import wukongim_client

    monkeypatch.setattr(customer_history, "history_scope", lambda _: (
        SimpleNamespace(author=None, selectors={}, replies={}, media={})
    ))
    messages = [
        SimpleNamespace(message_seq=i, from_uid="customer-vtr", payload=p)
        for i, p in enumerate([
            {"type": 1, "content": "first"}, invalid_payload,
            {"type": 1, "content": "last"},
        ])
    ]
    monkeypatch.setattr(wukongim_client, "sync_channel_messages",
                        AsyncMock(return_value=SimpleNamespace(
                            messages=messages,
                        )))
    result = await humanization_service.recent_customer_messages(
        "channel", 251, "customer-vtr",
    )
    assert [(item.role, item.content) for item in result] == [
        ("customer", "first"), ("customer", "last"),
    ]
