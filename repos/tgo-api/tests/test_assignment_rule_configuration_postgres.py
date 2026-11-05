"""Real HTTP reception settings with disposable PostgreSQL and staff JWTs."""

from uuid import uuid4

import httpx
import pytest

from app.core.security import create_access_token
from app.models import Project, Staff, VisitorAssignmentRule
from tests import test_authentication_boundaries_postgres as postgres_checks

TEST_SERVER = postgres_checks.TEST_SERVER
TEST_URL = postgres_checks.TEST_URL
postgres_engine = postgres_checks.postgres_engine
identity = postgres_checks.identity

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_URL or not TEST_SERVER,
        reason="Disposable PostgreSQL and isolated HTTP API are required",
    ),
]
PATH = "/api/v1/visitor-assignment-rules"


def headers(project, staff):
    return {
        "Authorization": "Bearer "
        + create_access_token(
            staff.username, project.id, token_version=staff.token_version
        )
    }


async def test_default_read_has_no_database_side_effect(identity):
    db, project, staff, _platform = identity
    async with httpx.AsyncClient(base_url=TEST_SERVER) as client:
        response = await client.get(PATH, headers=headers(project, staff))
    assert response.status_code == 200, response.text
    assert response.json()["service_weekdays"] == [1, 2, 3, 4, 5, 6, 7]
    assert response.json()["llm_assignment_enabled"] is False
    assert (
        db.query(VisitorAssignmentRule)
        .filter_by(project_id=project.id)
        .count()
        == 0
    )


async def test_first_save_and_reload_preserve_array_and_disabled_state(
    identity,
):
    db, project, staff, _platform = identity
    async with httpx.AsyncClient(
        base_url=TEST_SERVER, headers=headers(project, staff)
    ) as client:
        saved = await client.put(PATH, json={"max_concurrent_chats": 19})
        assert saved.status_code == 200, saved.text
        loaded = await client.get(PATH)
        assert loaded.status_code == 200, loaded.text
    assert saved.json() == loaded.json()
    rule = (
        db.query(VisitorAssignmentRule).filter_by(project_id=project.id).one()
    )
    assert rule.llm_assignment_enabled is False
    assert rule.service_weekdays == [1, 2, 3, 4, 5, 6, 7]
    assert rule.max_concurrent_chats == 19


@pytest.mark.parametrize("initial", [False, True])
async def test_partial_save_preserves_explicit_choice_over_http(
    identity, initial
):
    db, project, staff, _platform = identity
    async with httpx.AsyncClient(
        base_url=TEST_SERVER, headers=headers(project, staff)
    ) as client:
        assert (
            await client.put(PATH, json={"llm_assignment_enabled": initial})
        ).status_code == 200
        changed = await client.put(PATH, json={"service_weekdays": [5, 1, 5]})
        assert changed.status_code == 200, changed.text
        assert changed.json()["llm_assignment_enabled"] is initial
        assert changed.json()["service_weekdays"] == [1, 5]
        loaded = await client.get(PATH)
        assert loaded.json()["llm_assignment_enabled"] is initial
    db.expire_all()
    assert (
        db.query(VisitorAssignmentRule)
        .filter_by(project_id=project.id)
        .one()
        .llm_assignment_enabled
        is initial
    )


@pytest.mark.parametrize("existing", [False, True])
async def test_null_flag_never_reaches_database_constraint(identity, existing):
    db, project, staff, _platform = identity
    async with httpx.AsyncClient(
        base_url=TEST_SERVER, headers=headers(project, staff)
    ) as client:
        if existing:
            created = await client.put(
                PATH,
                json={"llm_assignment_enabled": True, "prompt": "original"},
            )
            assert created.status_code == 200, created.text
        response = await client.put(
            PATH,
            json={"llm_assignment_enabled": None, "prompt": "must-not-save"},
        )
    assert response.status_code == 422, response.text
    db.expire_all()
    rule = (
        db.query(VisitorAssignmentRule)
        .filter_by(project_id=project.id)
        .first()
    )
    if existing:
        assert rule.prompt == "original"
        assert rule.llm_assignment_enabled is True
    else:
        assert rule is None


async def test_current_project_cannot_read_or_change_another_project(identity):
    db, project, staff, _platform = identity
    marker = uuid4().hex
    other = Project(name="reception-test-" + marker, api_key="test-" + marker)
    db.add(other)
    db.flush()
    colleague = Staff(
        project_id=other.id,
        username="reception-test-" + marker,
        password_hash="invalid-fixture-hash",
        role="admin",
    )
    db.add(colleague)
    db.commit()
    async with httpx.AsyncClient(base_url=TEST_SERVER) as client:
        theirs = await client.put(
            PATH,
            headers=headers(other, colleague),
            json={"prompt": "other project", "max_concurrent_chats": 7},
        )
        assert theirs.status_code == 200, theirs.text
        own = await client.put(
            PATH,
            headers=headers(project, staff),
            json={"project_id": str(other.id), "max_concurrent_chats": 21},
        )
        assert own.status_code == 200, own.text
        assert own.json()["project_id"] == str(project.id)
        read = await client.get(
            PATH + "?project_id=" + str(other.id),
            headers=headers(project, staff),
        )
        assert read.json()["id"] == own.json()["id"]
        theirs_again = await client.get(
            PATH, headers=headers(other, colleague)
        )
        assert theirs_again.json() == theirs.json()


async def test_missing_provider_rejects_before_existing_fields_change(
    identity,
):
    _db, project, staff, _platform = identity
    async with httpx.AsyncClient(
        base_url=TEST_SERVER, headers=headers(project, staff)
    ) as client:
        original = await client.put(PATH, json={"prompt": "original"})
        assert original.status_code == 200, original.text
        denied = await client.put(
            PATH,
            json={"ai_provider_id": str(uuid4()), "prompt": "must-not-save"},
        )
        assert denied.status_code == 404, denied.text
        loaded = await client.get(PATH)
    assert loaded.json() == original.json()


async def test_clear_optional_prompt_and_model_persists(identity):
    db, project, staff, _platform = identity
    async with httpx.AsyncClient(
        base_url=TEST_SERVER, headers=headers(project, staff)
    ) as client:
        original = await client.put(
            PATH, json={"model": "fixture-model", "prompt": "custom"}
        )
        assert original.status_code == 200, original.text
        cleared = await client.put(PATH, json={"model": None, "prompt": None})
        assert cleared.status_code == 200, cleared.text
        loaded = await client.get(PATH)
    assert loaded.json() == cleared.json()
    db.expire_all()
    rule = (
        db.query(VisitorAssignmentRule).filter_by(project_id=project.id).one()
    )
    assert rule.model is None and rule.prompt is None


@pytest.mark.parametrize("method", ["get", "put"])
async def test_insufficient_permissions_block_real_jwt(identity, method):
    db, project, staff, _platform = identity
    staff.role = "user"
    db.commit()
    async with httpx.AsyncClient(base_url=TEST_SERVER) as client:
        response = await client.request(
            method,
            PATH,
            headers=headers(project, staff),
            **({"json": {}} if method == "put" else {}),
        )
    assert response.status_code == 403, response.text
    assert (
        db.query(VisitorAssignmentRule)
        .filter_by(project_id=project.id)
        .count()
        == 0
    )
