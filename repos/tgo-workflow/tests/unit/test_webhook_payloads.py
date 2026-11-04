"""Frozen delivery data and replay proofs are source-bound and confidential."""

import json
from uuid import uuid4

from pydantic import SecretStr
import pytest

from app.config import settings
from app.schemas.webhook_delivery import WebhookPayloadScope
from app.services.webhook_crypto import WebhookCryptoError
from app.services.webhook_payloads import (
    freeze_payload,
    open_payload,
    matches_payload,
)


@pytest.fixture
def scope(monkeypatch):
    monkeypatch.setattr(
        settings,
        "WORKFLOW_WEBHOOK_KEYS",
        SecretStr(
            json.dumps(
                {
                    "first": "synthetic-delivery-master-first-1234567890",
                }
            )
        ),
    )
    monkeypatch.setattr(settings, "WORKFLOW_WEBHOOK_ACTIVE_KEY_ID", "first")
    return WebhookPayloadScope(
        delivery_id=uuid4(),
        hook_id=uuid4(),
        project_id=uuid4(),
        workflow_id="synthetic-flow",
        actor_staff_id=uuid4(),
        binding_revision=1,
        credential_revision=1,
        event_key="a" * 64,
        release_id="fixed-release",
        release_digest="b" * 64,
    )


def test_encrypted_payload_roundtrip_and_proof_without_raw_data(scope):
    data = {"question": "synthetic-private-customer-question", "qty": 2}
    frozen = freeze_payload(scope, data)
    assert data["question"] not in frozen.ciphertext
    assert open_payload(scope, frozen.ciphertext) == data
    assert matches_payload(
        scope.hook_id, scope.event_key, data, frozen.key_id, frozen.receipt
    )
    assert not matches_payload(
        scope.hook_id,
        scope.event_key,
        {"qty": 3},
        frozen.key_id,
        frozen.receipt,
    )


@pytest.mark.parametrize(
    "field",
    [
        "delivery_id",
        "hook_id",
        "project_id",
        "actor_staff_id",
        "binding_revision",
        "credential_revision",
        "release_id",
    ],
)
def test_ciphertext_cannot_be_moved_to_changed_source_metadata(scope, field):
    frozen = freeze_payload(scope, {"test": "synthetic"})
    change = (
        2
        if "revision" in field
        else "other-release"
        if field == "release_id"
        else uuid4()
    )
    changed = scope.model_copy(update={field: change})
    with pytest.raises(WebhookCryptoError):
        open_payload(changed, frozen.ciphertext)


def test_rotation_retains_old_receipts_when_old_master_is_kept(
    scope, monkeypatch
):
    data = {"synthetic": True}
    frozen = freeze_payload(scope, data)
    monkeypatch.setattr(
        settings,
        "WORKFLOW_WEBHOOK_KEYS",
        SecretStr(
            json.dumps(
                {
                    "first": "synthetic-delivery-master-first-1234567890",
                    "second": "synthetic-delivery-master-second-1234567890",
                }
            )
        ),
    )
    monkeypatch.setattr(settings, "WORKFLOW_WEBHOOK_ACTIVE_KEY_ID", "second")
    assert open_payload(scope, frozen.ciphertext) == data
    assert matches_payload(
        scope.hook_id, scope.event_key, data, frozen.key_id, frozen.receipt
    )
    monkeypatch.setattr(
        settings,
        "WORKFLOW_WEBHOOK_KEYS",
        SecretStr(
            json.dumps(
                {
                    "second": "synthetic-delivery-master-second-1234567890",
                }
            )
        ),
    )
    with pytest.raises(WebhookCryptoError):
        matches_payload(
            scope.hook_id, scope.event_key, data, frozen.key_id, frozen.receipt
        )


def test_receipt_identity_cannot_cross_hooks_or_events(scope):
    frozen = freeze_payload(scope, {"qty": 2})
    assert not matches_payload(
        uuid4(), scope.event_key, {"qty": 2}, frozen.key_id, frozen.receipt
    )
    assert not matches_payload(
        scope.hook_id, "c" * 64, {"qty": 2}, frozen.key_id, frozen.receipt
    )
