"""Malformed credentials fail closed without disclosing private payloads."""
import logging
from datetime import timedelta
from uuid import uuid4

from jose import JWTError, jwt
import pytest

from app.core import security
from app.core.config import settings


@pytest.mark.parametrize("claim", ["exp", "iat", "nbf"])
@pytest.mark.parametrize("value", [None, {}, []])
def test_invalid_time_claims_are_rejected_without_raising(claim, value):
    token = jwt.encode(
        {"sub": "synthetic-staff", claim: value},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    assert security.verify_token(token) is None


@pytest.mark.parametrize("stored_hash", ["", "invalid-hash", "$2b$12$broken"])
def test_invalid_stored_password_hash_denies_authentication(stored_hash):
    assert security.verify_password("synthetic-input", stored_hash) is False


def test_valid_password_hash_accepts_only_the_correct_password():
    hashed = security.get_password_hash("synthetic-correct-input")
    assert security.verify_password("synthetic-correct-input", hashed) is True
    assert security.verify_password("synthetic-wrong-input", hashed) is False


def test_jwt_errors_do_not_log_private_exception_text(monkeypatch, caplog):
    private = "synthetic-private-token-claim"

    def fail(*args, **kwargs):
        raise JWTError(private)

    monkeypatch.setattr(security.jwt, "decode", fail)
    with caplog.at_level(logging.DEBUG, logger="security"):
        assert security.verify_token("synthetic-invalid-token") is None
    assert private not in caplog.text
    assert "JWTError" in caplog.text


def test_staff_token_preserves_legacy_and_current_claims():
    project = uuid4()
    token = security.create_access_token(
        "synthetic-staff", project, role="admin", token_version=2
    )
    claims = security.verify_token(token)
    assert claims is not None
    assert claims["sub"] == "synthetic-staff"
    assert claims["project_id"] == str(project)
    assert claims["role"] == "admin" and claims["token_version"] == 2
    legacy = jwt.encode(
        {"sub": "synthetic-staff", "custom": {"allowed": [True, None]}},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    legacy_claims = security.verify_token(legacy)
    assert legacy_claims is not None
    assert legacy_claims["custom"] == {"allowed": [True, None]}
    expired = security.create_access_token(
        "synthetic-staff", expires_delta=timedelta(seconds=-10)
    )
    assert security.verify_token(expired) is None
