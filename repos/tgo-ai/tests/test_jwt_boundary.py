"""Service JWT claims must fail authentication before project lookup."""

from datetime import timedelta
import traceback
from unittest.mock import AsyncMock

from jose import JWTError, jwt
from jose.exceptions import JOSEError
import pytest

from app.auth import jwt as authentication
from app.config import settings
from app.exceptions import AuthenticationError

PRIVATE = "SYNTHETIC-JWT-PRIVATE"
TEST_SECRET = "synthetic-jwt-test-signing-key"


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", TEST_SECRET)
    monkeypatch.setattr(settings, "algorithm", "HS256")


def assert_private(error):
    assert PRIVATE not in str(error)
    assert PRIVATE not in repr(error.details)
    assert PRIVATE not in "".join(traceback.format_exception(error))


def test_signed_claims_and_input_mapping_are_preserved():
    source = {"project_id": "11111111-1111-4111-8111-111111111111"}
    token = authentication.create_access_token(source, timedelta(minutes=1))
    decoded = authentication.verify_token(token)
    assert decoded["project_id"] == source["project_id"]
    assert isinstance(decoded["exp"], int)
    assert "exp" not in source


def test_other_valid_json_claims_are_preserved():
    claims = {
        "project_id": "11111111-1111-4111-8111-111111111111",
        "roles": ["reader"], "metadata": {"name": "测试"},
        "count": 2, "enabled": True, "empty": None,
    }
    token = jwt.encode(claims, TEST_SECRET, algorithm="HS256")
    assert authentication.verify_token(token) == claims


@pytest.mark.parametrize("kind", ["expired", "wrong-signature", "malformed"])
def test_invalid_signature_and_expiration_fail_closed(kind):
    if kind == "expired":
        token = authentication.create_access_token(
            {"project_id": "11111111-1111-4111-8111-111111111111"},
            timedelta(seconds=-1),
        )
    elif kind == "wrong-signature":
        token = jwt.encode({"project_id": "test"}, PRIVATE, algorithm="HS256")
    else:
        token = PRIVATE
    with pytest.raises(AuthenticationError) as captured:
        authentication.verify_token(token)
    assert_private(captured.value)


@pytest.mark.parametrize("payload", [None, [], PRIVATE, 42, {1: "value"}])
def test_malformed_decoder_output_is_not_trusted(monkeypatch, payload):
    monkeypatch.setattr(authentication.jwt, "decode", lambda *a, **k: payload)
    with pytest.raises(AuthenticationError) as captured:
        authentication.verify_token("synthetic")
    assert_private(captured.value)


def test_decoder_exception_does_not_expose_library_detail(monkeypatch):
    def fail(*args, **kwargs):
        raise JWTError(PRIVATE)

    monkeypatch.setattr(authentication.jwt, "decode", fail)
    with pytest.raises(AuthenticationError) as captured:
        authentication.verify_token("synthetic")
    assert_private(captured.value)


@pytest.mark.parametrize("value", [None, 12, b"synthetic", ""])
def test_encoder_must_return_a_nonempty_text_token(monkeypatch, value):
    monkeypatch.setattr(authentication.jwt, "encode", lambda *a, **k: value)
    with pytest.raises(AuthenticationError):
        authentication.create_access_token({"project_id": "test"})


@pytest.mark.parametrize("value", [12, True, [], {}, None, "", PRIVATE])
@pytest.mark.asyncio
async def test_invalid_project_claim_never_queries_database(value):
    token = jwt.encode({"project_id": value}, TEST_SECRET, algorithm="HS256")
    database = AsyncMock()
    with pytest.raises(AuthenticationError) as captured:
        await authentication.get_project_from_jwt(token, database)
    database.get.assert_not_awaited()
    assert_private(captured.value)


@pytest.mark.asyncio
async def test_valid_token_resolves_the_existing_project(
    db_session, test_project,
):
    token = authentication.create_access_token(
        {"project_id": str(test_project.id)},
    )
    actual = await authentication.get_project_from_jwt(token, db_session)
    assert actual is test_project


@pytest.mark.asyncio
async def test_deleted_project_is_still_rejected(db_session, test_project):
    test_project.soft_delete()
    await db_session.commit()
    token = authentication.create_access_token(
        {"project_id": str(test_project.id)},
    )
    with pytest.raises(AuthenticationError, match="Project is deleted"):
        await authentication.get_project_from_jwt(token, db_session)


def test_encoder_failure_does_not_expose_key_detail(monkeypatch):
    def fail(*args, **kwargs):
        raise JOSEError(PRIVATE)

    monkeypatch.setattr(authentication.jwt, "encode", fail)
    with pytest.raises(AuthenticationError) as captured:
        authentication.create_access_token({"project_id": "test"})
    assert_private(captured.value)


@pytest.mark.asyncio
async def test_missing_project_remains_rejected():
    token = authentication.create_access_token(
        {"project_id": "11111111-1111-4111-8111-111111111111"},
    )
    database = AsyncMock()
    database.get.return_value = None
    with pytest.raises(AuthenticationError, match="Project not found"):
        await authentication.get_project_from_jwt(token, database)
    database.get.assert_awaited_once()
