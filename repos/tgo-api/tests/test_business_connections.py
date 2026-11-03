"""Tenant connection ownership, credential isolation and revision lifecycle."""

from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError, TGOAPIException
from app.models.business_connection import BusinessConnection
from app.schemas.business_connections import (
    BusinessConnectionCreate,
    BusinessConnectionUpdate,
    BusinessConnectionCredentials,
)
from app.services import business_connections as connections
from app.services.business_connection_vault import CredentialVault


@pytest.fixture
def db(monkeypatch):
    import base64
    import os

    monkeypatch.setattr(
        settings,
        "BUSINESS_CONNECTION_CREDENTIAL_KEY",
        SecretStr(base64.urlsafe_b64encode(os.urandom(32)).decode()),
    )
    engine = create_engine("sqlite:///:memory:")
    BusinessConnection.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def request(**overrides):
    return BusinessConnectionCreate(
        request_id=uuid4(),
        name="澜月订单连接",
        mode="http",
        configuration={
            "base_url": "https://orders.example.test",
            "auth_mode": "bearer",
        },
        credentials={"token": "fixture-credential-not-a-real-secret"},
        **overrides,
    )


def test_credentials_are_scope_bound_encrypted_and_absent_from_public_views(db):
    project, actor = uuid4(), uuid4()
    saved = connections.create(db, project, actor, request())
    row = db.get(BusinessConnection, saved.id)
    assert (
        row.credential_ciphertext
        and "fixture-credential" not in row.credential_ciphertext
    )
    for view in (
        saved,
        connections.get(db, project, saved.id),
        connections.list_connections(db, project)[0],
    ):
        assert "fixture-credential" not in view.model_dump_json()
        assert "credential_ciphertext" not in view.model_dump_json()
        assert view.enabled is False and view.test_status == "not_tested"
    decrypted = CredentialVault().decrypt(row.credential_ciphertext, project, row.id)
    assert decrypted.token.get_secret_value() == "fixture-credential-not-a-real-secret"
    with pytest.raises(TGOAPIException):
        CredentialVault().decrypt(row.credential_ciphertext, uuid4(), row.id)
    with pytest.raises(TGOAPIException):
        CredentialVault().decrypt(row.credential_ciphertext, project, uuid4())


def test_idempotent_creation_survives_session_reload_without_overwriting_later_edit(db):
    project, actor = uuid4(), uuid4()
    payload = request()
    saved = connections.create(db, project, actor, payload)
    changed = connections.update(
        db,
        project,
        actor,
        saved.id,
        BusinessConnectionUpdate(expected_revision=1, name="新的连接名称"),
    )
    db.expire_all()
    replay = connections.create(db, project, actor, payload)
    assert (
        replay.id == saved.id and replay.name == changed.name and replay.revision == 2
    )
    assert len(connections.list_connections(db, project)) == 1
    with pytest.raises(ConflictError):
        connections.create(
            db, project, actor, payload.model_copy(update={"name": "different request"})
        )


def test_original_creation_can_replay_after_merchant_fields_are_added(db):
    import hashlib
    import hmac
    import json

    from app.services.business_connection_vault import credential_values

    project, actor = uuid4(), uuid4()
    payload = request()
    saved = connections.create(db, project, actor, payload)
    # Simulate the canonical request recorded by the previous API version.
    legacy_fields = {
        "base_url",
        "order_path",
        "logistics_path",
        "method",
        "timeout_seconds",
        "auth_mode",
        "api_key_header",
        "data_path",
        "success_field",
        "success_value",
        "tenant_id_field",
        "customer_id_field",
        "order_no_field",
        "order_status_field",
        "order_amount_field",
        "order_amount_unit",
        "order_currency_field",
        "order_created_at_field",
        "logistics_status_field",
        "logistics_carrier_field",
        "logistics_tracking_no_field",
        "logistics_updated_at_field",
    }
    original = payload.model_dump(mode="json", exclude={"credentials"})
    original["configuration"] = {
        key: value
        for key, value in original["configuration"].items()
        if key in legacy_fields
    }
    original["credentials"] = credential_values(payload.credentials)
    raw = json.dumps(
        original, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    row = db.get(BusinessConnection, saved.id)
    row.request_digest = hmac.new(
        settings.SECRET_KEY.encode(),
        b"business-connection-create:" + raw,
        hashlib.sha256,
    ).hexdigest()
    db.commit()
    connections.update(
        db,
        project,
        actor,
        saved.id,
        BusinessConnectionUpdate(expected_revision=1, name="后续改名"),
    )
    db.expire_all()
    replay = connections.create(db, project, actor, payload)
    assert replay.id == saved.id and replay.name == "后续改名" and replay.revision == 2
    changed = payload.model_copy(
        update={
            "configuration": payload.configuration.model_copy(
                update={"merchant_id": "changed-shop"}
            )
        }
    )
    with pytest.raises(ConflictError):
        connections.create(db, project, actor, changed)


def test_original_creation_can_replay_after_customer_fields_are_added(db):
    import hashlib
    import hmac
    import json

    from app.services.business_connection_vault import credential_values

    project, actor = uuid4(), uuid4()
    payload = request()
    saved = connections.create(db, project, actor, payload)
    original = payload.model_dump(mode="json", exclude={"credentials"})
    for name in (
        "customer_profile_path", "membership_path", "customer_segment_field",
        "customer_tags_field", "membership_tier_field", "membership_points_field",
    ):
        original["configuration"].pop(name)
    original["credentials"] = credential_values(payload.credentials)
    raw = json.dumps(
        original, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    row = db.get(BusinessConnection, saved.id)
    row.request_digest = hmac.new(
        settings.SECRET_KEY.encode(),
        b"business-connection-create:" + raw,
        hashlib.sha256,
    ).hexdigest()
    db.commit()

    assert connections.create(db, project, actor, payload).id == saved.id
    changed = payload.model_copy(
        update={
            "configuration": payload.configuration.model_copy(
                update={
                    "customer_profile_path": "/v1/customers/profile/query",
                    "membership_path": "/v1/memberships/query",
                }
            )
        }
    )
    with pytest.raises(ConflictError):
        connections.create(db, project, actor, changed)


def test_other_tenant_cannot_read_update_rotate_or_resolve_a_connection(db):
    saved = connections.create(db, uuid4(), uuid4(), request())
    foreign = uuid4()
    assert connections.list_connections(db, foreign) == []
    with pytest.raises(NotFoundError):
        connections.get(db, foreign, saved.id)
    with pytest.raises(NotFoundError):
        connections.update(
            db,
            foreign,
            uuid4(),
            saved.id,
            BusinessConnectionUpdate(expected_revision=1, name="changed"),
        )
    with pytest.raises(NotFoundError):
        connections.resolve(db, foreign, saved.id, expected_revision=1)


def test_configuration_and_credential_rotation_invalidate_tests_but_names_do_not(db):
    project, actor = uuid4(), uuid4()
    saved = connections.create(db, project, actor, request())
    row = db.get(BusinessConnection, saved.id)
    row.verified_configuration_revision = 1
    row.verified_capabilities = ["order_query", "logistics_query"]
    db.commit()
    before_name = connections.get(db, project, saved.id)
    named = connections.update(
        db,
        project,
        actor,
        saved.id,
        BusinessConnectionUpdate(expected_revision=1, name="订单连接"),
    )
    assert named.configuration_revision == 1
    assert named.resource_revision == before_name.resource_revision
    rotated = connections.update(
        db,
        project,
        actor,
        saved.id,
        BusinessConnectionUpdate(
            expected_revision=2,
            credentials=BusinessConnectionCredentials(
                token=SecretStr("second-fixture-credential")
            ),
        ),
    )
    assert rotated.configuration_revision == 2 and rotated.test_status == "not_tested"
    assert rotated.resource_revision != named.resource_revision
    assert db.get(BusinessConnection, saved.id).verified_capabilities == []
    changed = connections.update(
        db,
        project,
        actor,
        saved.id,
        BusinessConnectionUpdate(
            expected_revision=3,
            configuration={
                "base_url": "https://orders.example.test",
                "auth_mode": "bearer",
                "order_path": "/v2/orders/query",
            },
        ),
    )
    assert changed.configuration_revision == 3


def test_unknown_test_proof_and_stale_revision_cannot_enable_or_rotate(db):
    project, actor = uuid4(), uuid4()
    saved = connections.create(db, project, actor, request())
    with pytest.raises(ConflictError):
        connections.update(
            db,
            project,
            actor,
            saved.id,
            BusinessConnectionUpdate(expected_revision=1, enabled=True),
        )
    assert connections.get(db, project, saved.id).enabled is False
    connections.update(
        db,
        project,
        actor,
        saved.id,
        BusinessConnectionUpdate(expected_revision=1, name="changed"),
    )
    with pytest.raises(ConflictError):
        connections.update(
            db,
            project,
            actor,
            saved.id,
            BusinessConnectionUpdate(
                expected_revision=1, credentials={"token": "old-conflicting-credential"}
            ),
        )
    row = db.get(BusinessConnection, saved.id)
    assert (
        CredentialVault()
        .decrypt(row.credential_ciphertext, project, row.id)
        .token.get_secret_value()
        == "fixture-credential-not-a-real-secret"
    )


def test_missing_vault_key_fails_closed_and_does_not_store_plaintext(db, monkeypatch):
    monkeypatch.setattr(settings, "BUSINESS_CONNECTION_CREDENTIAL_KEY", None)
    with pytest.raises(TGOAPIException):
        connections.create(db, uuid4(), uuid4(), request())
    assert db.query(BusinessConnection).count() == 0


def test_directory_exposes_only_current_tenant_metadata_and_never_test_success(
    db, monkeypatch
):
    from app.services.scenario_resources import connection_catalog

    project = uuid4()
    saved = connections.create(db, project, uuid4(), request())
    section = connection_catalog(db, project)
    assert section.status == "available" and len(section.items) == 1
    assert (
        section.items[0].id == str(saved.id) and section.items[0].status == "not_ready"
    )
    assert "fixture-credential" not in section.model_dump_json()
    assert "base_url" not in section.model_dump_json()
    assert connection_catalog(db, uuid4()).items == []
    monkeypatch.setattr(settings, "BUSINESS_CONNECTION_CREDENTIAL_KEY", None)
    section = connection_catalog(db, project)
    assert section.items[0].status == "unknown"
    assert section.items[0].details.resource_revision != saved.resource_revision


@pytest.mark.parametrize(
    "url",
    [
        "http://orders.example.test",
        "https://user:credential@orders.example.test",
        "https://orders.example.test?token=credential",
        "https://orders.example.test#fragment",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://[::1]",
        "https://orders.example.test:8443",
        "https://orders.example.test/private/path",
    ],
)
def test_unsafe_targets_never_enter_saved_configuration(url):
    with pytest.raises(ValidationError):
        BusinessConnectionCreate(
            request_id=uuid4(),
            name="连接",
            mode="http",
            configuration={"base_url": url},
        )


@pytest.mark.parametrize(
    "path",
    [
        "//external.test",
        "/a/../b",
        "/a/%2e%2e/b",
        "/api?token=x",
        "/api#fragment",
        "/a\\b",
    ],
)
def test_query_paths_cannot_escape_the_fixed_origin(path):
    with pytest.raises(ValidationError):
        BusinessConnectionCreate(
            request_id=uuid4(),
            name="连接",
            mode="http",
            configuration={
                "base_url": "https://orders.example.test",
                "order_path": path,
            },
        )
