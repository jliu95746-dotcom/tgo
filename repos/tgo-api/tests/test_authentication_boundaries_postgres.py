"""Exercise staff HTTP routes and registration checks in an isolated DB."""
import os
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from jose import jwt
from pydantic import SecretStr
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base
from app.core.exceptions import TGOAPIException
from app.core.security import create_access_token
from app.models import Platform, Project, Staff
from app.services.visitor_registration_output import RegistrationOrigin

TEST_URL = os.getenv("SCENARIOS_POSTGRES_TEST_URL")
TEST_SERVER = os.getenv("SCENARIOS_API_TEST_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL or not TEST_SERVER,
    reason="Disposable PostgreSQL and isolated HTTP API are required",
)


@pytest.fixture(scope="module")
def postgres_engine():
    assert TEST_URL is not None and TEST_SERVER is not None
    url = make_url(TEST_URL).set(drivername="postgresql+psycopg2")
    assert (url.database or "").startswith("tgo_scenarios_test_")
    server = urlsplit(TEST_SERVER)
    assert server.scheme == "http" and server.hostname == "api"
    engine = create_engine(url, hide_parameters=True)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def identity(postgres_engine):
    marker = uuid4().hex
    with Session(postgres_engine) as db:
        project = Project(name="auth-test-" + marker, api_key="test-" + marker)
        db.add(project)
        db.flush()
        staff = Staff(
            project_id=project.id,
            username="auth-test-" + marker,
            password_hash="invalid-hash",
            role="admin",
        )
        platform = Platform(
            project_id=project.id,
            name="auth-test-" + marker,
            type="website",
            api_key="platform-test-" + marker,
        )
        db.add_all([staff, platform])
        db.commit()
        yield db, project, staff, platform


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["exp", "iat", "nbf"])
@pytest.mark.parametrize("value", [None, {}, []])
async def test_malformed_claims_return_http_401(identity, claim, value):
    _, project, staff, _ = identity
    token = jwt.encode(
        {"sub": staff.username, "project_id": str(project.id), claim: value},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    async with httpx.AsyncClient(base_url=TEST_SERVER) as client:
        response = await client.get(
            "/api/v1/staff/me", headers={"Authorization": "Bearer " + token}
        )
    assert response.status_code == 401
    assert token not in response.text


@pytest.mark.asyncio
async def test_current_membership_and_token_version_over_http(identity):
    db, project, staff, _ = identity
    token = create_access_token(staff.username, project.id)
    async with httpx.AsyncClient(base_url=TEST_SERVER) as client:

        async def request(access_token):
            return await client.get(
                "/api/v1/staff/me",
                headers={"Authorization": "Bearer " + access_token},
            )

        initial = await request(token)
        assert initial.status_code == 200
        assert initial.json()["id"] == str(staff.id)
        staff.account_enabled = False
        db.commit()
        assert (await request(token)).status_code == 401
        staff.account_enabled = True
        staff.token_version = 2
        db.commit()
        assert (await request(token)).status_code == 401
        current = create_access_token(
            staff.username, project.id, token_version=2
        )
        assert (await request(current)).status_code == 200
        wrong_project = create_access_token(
            staff.username, uuid4(), token_version=2
        )
        assert (await request(wrong_project)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_hash", ["", "invalid-hash", "$2b$12$broken"])
async def test_invalid_hash_returns_http_401(identity, stored_hash):
    db, _, staff, _ = identity
    staff.password_hash = stored_hash
    db.commit()
    async with httpx.AsyncClient(base_url=TEST_SERVER) as client:
        response = await client.post(
            "/api/v1/staff/login",
            data={"username": staff.username, "password": "synthetic-input"},
        )
    assert response.status_code == 401
    assert "access_token" not in response.json()


@pytest.mark.parametrize(
    "replacement", [None, "replacement-test-key", "synthetic-非ASCII-key"]
)
def test_registration_key_revocation_is_denied(identity, replacement):
    db, project, _, platform = identity
    origin = RegistrationOrigin(
        project.id, platform.id, SecretStr(platform.api_key)
    )
    origin.check(db)
    platform.api_key = replacement
    db.commit()
    with pytest.raises(TGOAPIException) as rejected:
        origin.check(db)
    assert rejected.value.status_code == 401
