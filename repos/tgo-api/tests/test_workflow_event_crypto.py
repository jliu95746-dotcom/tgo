"""Frozen deliveries survive key rotation without using a new event body."""

import base64
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from fastapi import HTTPException

from app.core.config import settings
from app.services.event_keyring import EventKeyringError
from app.services import workflow_event_subscriptions as subscriptions
from app.schemas.workflow_event_subscriptions import (
    WorkflowEventSubscriptionUpdate,
)
from tests.test_workflow_event_subscriptions import store, enable  # noqa: F401
from app.services.workflow_event_crypto import (
    decrypt_event_payload,
    encrypt_event_payload,
)

OLD = "old-delivery-material-for-tests-0123456789"
NEW = "new-delivery-material-for-tests-0123456789"


def configure(monkeypatch, keys=None, active=None, secret=OLD):
    monkeypatch.setattr(settings, "SECRET_KEY", secret)
    monkeypatch.setattr(
        settings,
        "WORKFLOW_EVENT_DELIVERY_KEYS",
        SecretStr(json.dumps(keys)) if keys is not None else None,
    )
    monkeypatch.setattr(
        settings, "WORKFLOW_EVENT_DELIVERY_ACTIVE_KEY_ID", active
    )


def test_modern_deliveries_use_original_key_after_both_keys_rotate(
    monkeypatch,
):
    configure(monkeypatch, {"delivery1": OLD}, "delivery1")
    encrypted = encrypt_event_payload('{"text":"固定片段"}')
    assert encrypted.startswith("ev1:delivery1:")
    configure(
        monkeypatch,
        {"delivery1": OLD, "delivery2": NEW},
        "delivery2",
        secret=NEW,
    )
    assert decrypt_event_payload(encrypted) == '{"text":"固定片段"}'
    assert encrypt_event_payload("new").startswith("ev1:delivery2:")


def test_unversioned_legacy_ciphertext_can_be_imported(monkeypatch):
    legacy = (
        Fernet(base64.urlsafe_b64encode(hashlib.sha256(OLD.encode()).digest()))
        .encrypt(b"old body")
        .decode()
    )
    configure(monkeypatch)
    assert decrypt_event_payload(legacy) == "old body"
    assert (
        decrypt_event_payload(encrypt_event_payload("before migration"))
        == "before migration"
    )
    configure(
        monkeypatch, {"legacy": OLD, "delivery2": NEW}, "delivery2", secret=NEW
    )
    assert decrypt_event_payload(legacy) == "old body"
    configure(monkeypatch, {"delivery2": NEW}, "delivery2", secret=NEW)
    with pytest.raises(EventKeyringError):
        decrypt_event_payload(legacy)


def test_unknown_key_is_retryable_but_invalid_ciphertext_is_not(monkeypatch):
    configure(monkeypatch, {"delivery1": OLD}, "delivery1")
    encrypted = encrypt_event_payload("body")
    configure(monkeypatch, {"delivery2": NEW}, "delivery2", secret=OLD)
    with pytest.raises(EventKeyringError):
        decrypt_event_payload(encrypted)
    with pytest.raises(ValueError):
        decrypt_event_payload("ev1:delivery2:not-a-fernet-token")
    with pytest.raises(ValueError):
        decrypt_event_payload("ev2:delivery2:unknown-version")


def test_invalid_explicit_config_never_uses_legacy_secret(monkeypatch):
    configure(monkeypatch, {}, "missing")
    with pytest.raises(EventKeyringError) as error:
        encrypt_event_payload("private body")
    assert OLD not in str(error.value) and "private body" not in str(
        error.value
    )


def test_ciphertext_header_is_bound_even_if_operator_reuses_material(
    monkeypatch,
):
    configure(monkeypatch, {"delivery1": OLD, "delivery2": OLD}, "delivery1")
    encrypted = encrypt_event_payload("body")
    with pytest.raises(ValueError):
        decrypt_event_payload(
            encrypted.replace("ev1:delivery1:", "ev1:delivery2:")
        )


@pytest.mark.asyncio
async def test_bad_encryption_config_blocks_enable_but_allows_stop(
    store, monkeypatch
):
    db, actor = store
    configure(monkeypatch, {}, "unknown")
    remote = AsyncMock()
    monkeypatch.setattr(
        subscriptions.workflow_client, "get_event_test_context", remote
    )
    with pytest.raises(HTTPException) as error:
        await subscriptions.update_subscription(db, actor, "flow", enable())
    assert error.value.status_code == 503
    assert OLD not in str(error.value.detail)
    remote.assert_not_awaited()
    assert (
        subscriptions.get_subscription(db, actor.project_id, "flow").revision
        == 0
    )
    disabled = await subscriptions.update_subscription(
        db,
        actor,
        "flow",
        WorkflowEventSubscriptionUpdate(enabled=False, expected_revision=0),
    )
    assert not disabled.enabled
