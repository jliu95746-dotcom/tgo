"""Only a registered merchant issuer can create scoped customer sessions."""

import base64
import hashlib
import hmac
import json
import os
from functools import partial
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models.business_connection import BusinessConnection
from app.models.business_customer_identity import (
    BusinessCustomerBinding,
    BusinessCustomerIdentityPolicy,
)
from app.models.visitor_business_scope import VisitorBusinessScope
from app.schemas.business_customer_identity import (
    CustomerIdentityPolicyWrite,
)
from app.services.business_customer_identity import BusinessCustomerIdentityService
from app.services.visitor_access_crypto import access_digest


KEY = "fixture-only-merchant-signing-key-00000000"
VIEWER_SECRET = SecretStr("fixture-only-owned-browser-access")


def sign(claims, key=KEY):
    encoded = (
        base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        key.encode(), b"tgo-customer-identity-v1:" + encoded.encode(), hashlib.sha256
    ).hexdigest()
    return SecretStr("v1." + encoded + "." + signature)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_CREDENTIAL_KEY",
        SecretStr(base64.urlsafe_b64encode(os.urandom(32)).decode()),
    )
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (
        BusinessConnection,
        BusinessCustomerIdentityPolicy,
        BusinessCustomerBinding,
        VisitorBusinessScope,
    ):
        model.__table__.create(engine)
    project, connection, platform, visitor, actor = [uuid4() for _ in range(5)]
    current = SimpleNamespace(
        id=visitor,
        project_id=project,
        platform_id=platform,
        deleted_at=None,
        platform=SimpleNamespace(type="website"),
        access_secret_digest=None,
    )
    current.access_secret_digest = access_digest(current, VIEWER_SECRET)
    with Session(engine) as db:
        db.add(
            BusinessConnection(
                id=connection,
                project_id=project,
                request_id=uuid4(),
                request_digest="a" * 64,
                name="测试商户",
                mode="http",
                configuration={"base_url": "https://merchant.example.test"},
                capabilities=["order_query"],
                enabled=False,
                revision=1,
                configuration_revision=1,
                authorization_revision=1,
            )
        )
        db.commit()
        service = BusinessCustomerIdentityService(
            db,
            visitor_reader=lambda p, v: (
                current if (p, v) == (project, visitor) else None
            ),
            platform_reader=lambda p, x: (p, x) == (project, platform),
            actor_reader=lambda p, a: (p, a) == (project, actor),
        )
        # Unit callers own the fixture browser; actual HTTP cases send this factor explicitly.
        service.exchange = partial(service.exchange, visitor_access_token=VIEWER_SECRET)
        policy = service.configure(
            project,
            actor,
            connection,
            CustomerIdentityPolicyWrite(
                expected_revision=0,
                platform_ids=[platform],
                signing_secret=SecretStr(KEY),
                enabled=True,
                max_session_seconds=900,
            ),
        )
        yield service, db, project, connection, platform, visitor, actor, policy
    engine.dispose()


def claims(setup, **changes):
    _, _, project, connection, platform, visitor, _, policy = setup
    now = int(datetime.now(UTC).timestamp())
    return {
        "purpose": "business_customer_identity",
        "project_id": str(project),
        "connection_id": str(connection),
        "platform_id": str(platform),
        "visitor_id": str(visitor),
        "customer_id": "merchant-member-private-001",
        "nonce": str(uuid4()),
        "policy_revision": policy.revision,
        "issued_at": now,
        "expires_at": now + 300,
        **changes,
    }


def test_verified_session_is_scoped_encrypted_and_not_an_execution_grant(setup):
    service, db, project, connection, _, visitor, _, policy = setup
    response = service.exchange(connection, sign(claims(setup)))
    assert (
        response.can_enable is False and response.scope == "business_customer_identity"
    )
    assert "merchant-member-private-001" not in response.model_dump_json()
    assert KEY not in policy.model_dump_json()
    verified = service.resolve(project, connection, visitor, response.session_token)
    assert (
        verified.customer_id.get_secret_value() == "merchant-member-private-001"
        and verified.can_enable is False
    )
    row = db.get(BusinessCustomerBinding, response.binding_id)
    assert "merchant-member-private-001" not in row.customer_ciphertext
    assert row.proof_digest and "v1." not in row.proof_digest


def test_same_nonce_restores_same_session_without_renewing_or_overwriting(setup):
    service, db, _, connection, _, _, _, _ = setup
    original = claims(setup)
    first = service.exchange(connection, sign(original))
    second = service.exchange(connection, sign(original))
    assert (
        first.binding_id == second.binding_id and first.expires_at == second.expires_at
    )
    assert (
        first.session_token.get_secret_value()
        == second.session_token.get_secret_value()
    )
    with pytest.raises(TGOAPIException):
        service.exchange(
            connection, sign({**original, "customer_id": "different-customer"})
        )
    assert len(list(db.scalars(select(BusinessCustomerBinding)))) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"project_id": str(uuid4())},
        {"connection_id": str(uuid4())},
        {"platform_id": str(uuid4())},
        {"visitor_id": str(uuid4())},
        {"policy_revision": 99},
        {"purpose": "configuration_test"},
        {"expires_at": 1},
        {"issued_at": 1},
        {"expires_at": int(datetime.now(UTC).timestamp()) + 7200},
    ],
)
def test_wrong_identity_scope_or_time_is_rejected(setup, changes):
    service, _, _, connection, _, _, _, _ = setup
    with pytest.raises(TGOAPIException):
        service.exchange(connection, sign(claims(setup, **changes)))


def test_signature_rotation_and_revoke_close_existing_sessions(setup):
    service, _, project, connection, platform, visitor, actor, _ = setup
    proof = claims(setup)
    with pytest.raises(TGOAPIException):
        service.exchange(connection, sign(proof, "wrong-signer-key-0000000000000000"))
    first = service.exchange(connection, sign(proof))
    service.revoke(project, actor, connection, first.binding_id, 1)
    with pytest.raises(TGOAPIException):
        service.resolve(project, connection, visitor, first.session_token)
    with pytest.raises(TGOAPIException):
        service.exchange(connection, sign(proof))
    second = service.exchange(connection, sign(claims(setup)))
    service.configure(
        project,
        actor,
        connection,
        CustomerIdentityPolicyWrite(
            expected_revision=1,
            platform_ids=[platform],
            signing_secret=SecretStr("rotated-merchant-key-00000000000000"),
            enabled=True,
        ),
    )
    with pytest.raises(TGOAPIException):
        service.resolve(project, connection, visitor, second.session_token)


def test_customer_session_cannot_be_used_for_other_visitor_or_connection(setup):
    service, _, project, connection, _, visitor, _, _ = setup
    response = service.exchange(connection, sign(claims(setup)))
    for p, c, v in [
        (uuid4(), connection, visitor),
        (project, uuid4(), visitor),
        (project, connection, uuid4()),
    ]:
        with pytest.raises(TGOAPIException):
            service.resolve(p, c, v, response.session_token)


def test_client_profile_edits_are_not_an_identity_source(setup):
    service, _, _, connection, _, _, _, _ = setup
    with pytest.raises(TGOAPIException):
        service.exchange(
            connection,
            SecretStr(
                json.dumps(
                    {"business_customer_id": "guessed", "platform_open_id": "guessed"}
                )
            ),
        )


def test_duplicate_json_keys_are_rejected_even_with_valid_signature(setup):
    service, _, _, connection, _, _, _, _ = setup
    body = json.dumps(claims(setup))[:-1] + ',"customer_id":"replacement"}'
    encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    signature = hmac.new(
        KEY.encode(), b"tgo-customer-identity-v1:" + encoded.encode(), hashlib.sha256
    ).hexdigest()
    with pytest.raises(TGOAPIException):
        service.exchange(connection, SecretStr("v1." + encoded + "." + signature))


@pytest.mark.parametrize(
    "change",
    ["configuration", "channel", "visitor", "disable", "expiry", "wrong_audience"],
)
def test_owner_changes_expiry_and_other_token_purposes_close_sessions(setup, change):
    from jose import jwt
    from app.services.business_customer_identity_crypto import session_signing_key

    service, db, project, connection, platform, visitor, actor, _ = setup
    response = service.exchange(connection, sign(claims(setup)))
    if change == "configuration":
        row = db.get(BusinessConnection, connection)
        row.configuration_revision += 1
        db.commit()
    elif change == "channel":
        service.platform_reader = lambda p, x: False
    elif change == "visitor":
        service.visitor_reader = lambda p, x: None
    elif change == "disable":
        service.configure(
            project,
            actor,
            connection,
            CustomerIdentityPolicyWrite(
                expected_revision=1, platform_ids=[platform], enabled=False
            ),
        )
    elif change == "expiry":
        row = db.get(BusinessCustomerBinding, response.binding_id)
        row.issued_at = datetime.fromtimestamp(
            int(datetime.now(UTC).timestamp()) - 600, UTC
        )
        row.expires_at = datetime.fromtimestamp(
            int(datetime.now(UTC).timestamp()) - 1, UTC
        )
        db.commit()
    else:
        token = jwt.encode(
            {
                "sub": str(response.binding_id),
                "aud": "staff",
                "exp": int(datetime.now(UTC).timestamp()) + 60,
            },
            session_signing_key(),
            algorithm="HS256",
        )
        response = response.model_copy(update={"session_token": SecretStr(token)})
    with pytest.raises(TGOAPIException):
        service.resolve(project, connection, visitor, response.session_token)
    if change in ("channel", "visitor"):
        assert service.list_bindings(project, connection)[0].status == "stale"


def test_recovery_of_existing_nonce_after_fresh_issue_window_does_not_renew_session(
    setup, monkeypatch
):
    from datetime import timedelta
    from app.services import business_customer_identity as module

    service, _, _, connection, _, _, _, _ = setup
    original_time = datetime.now(UTC) - timedelta(seconds=120)
    proof = claims(
        setup,
        issued_at=int(original_time.timestamp()),
        expires_at=int(original_time.timestamp()) + 300,
    )

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return original_time

    monkeypatch.setattr(module, "datetime", Clock)
    first = service.exchange(connection, sign(proof))
    monkeypatch.setattr(module, "datetime", datetime)
    recovered = service.exchange(connection, sign(proof))
    assert (
        recovered.binding_id == first.binding_id
        and recovered.expires_at == first.expires_at
    )
    assert recovered.session_token == first.session_token
