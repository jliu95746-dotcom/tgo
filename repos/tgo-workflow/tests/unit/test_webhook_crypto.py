"""Webhook credentials remain encrypted and bound to their owning source."""

import json
from uuid import uuid4

from pydantic import SecretStr
import pytest

from app.config import settings
from app.services.webhook_crypto import (
    WebhookCryptoError,
    ensure_credential_crypto,
    open_credential,
    seal_credential,
)

FIRST = "synthetic-webhook-master-first-1234567890"
SECOND = "synthetic-webhook-master-second-1234567890"
SECRET = SecretStr("synthetic-external-sender-credential-1234567890")


def ring(monkeypatch, keys=None, active="first"):
    monkeypatch.setattr(
        settings,
        "WORKFLOW_WEBHOOK_KEYS",
        SecretStr(json.dumps(keys or {"first": FIRST})),
        raising=False,
    )
    monkeypatch.setattr(
        settings,
        "WORKFLOW_WEBHOOK_ACTIVE_KEY_ID",
        active,
        raising=False,
    )


def scope():
    return dict(hook_id=uuid4(), credential_revision=1, key_id="sender-v1")


def test_encrypts_with_randomness_and_never_returns_the_master(monkeypatch):
    ring(monkeypatch)
    owner = scope()
    first = seal_credential(**owner, secret=SECRET)
    second = seal_credential(**owner, secret=SECRET)
    assert first.startswith("wh1:first:") and first != second
    assert FIRST not in first and SECRET.get_secret_value() not in first
    result = open_credential(**owner, encrypted=first)
    assert result == SECRET and SECRET.get_secret_value() not in repr(result)


@pytest.mark.parametrize(
    "change", ["hook_id", "credential_revision", "key_id"]
)
def test_ciphertext_cannot_be_transplanted(monkeypatch, change):
    ring(monkeypatch)
    owner = scope()
    encrypted = seal_credential(**owner, secret=SECRET)
    replacements = dict(hook_id=uuid4(), credential_revision=2, key_id="other")
    owner[change] = replacements[change]
    with pytest.raises(WebhookCryptoError) as failure:
        open_credential(**owner, encrypted=encrypted)
    assert FIRST not in str(failure.value)
    assert SECRET.get_secret_value() not in str(failure.value)


def test_retained_master_decrypts_after_rotation_but_missing_master_fails(
    monkeypatch,
):
    ring(monkeypatch)
    owner = scope()
    original = seal_credential(**owner, secret=SECRET)
    ring(monkeypatch, {"first": FIRST, "second": SECOND}, "second")
    assert open_credential(**owner, encrypted=original) == SECRET
    rotated = seal_credential(**owner, secret=SECRET)
    assert rotated.startswith("wh1:second:")
    ring(monkeypatch, {"second": SECOND}, "second")
    with pytest.raises(WebhookCryptoError):
        open_credential(**owner, encrypted=original)
    assert open_credential(**owner, encrypted=rotated) == SECRET


@pytest.mark.parametrize(
    "encoded,active",
    [
        (None, None),
        ("", None),
        ('{"a":"short"}', "a"),
        ('{"a":"' + FIRST + '"}', None),
        ('{"legacy":"' + FIRST + '"}', "legacy"),
        ('{"a":"' + FIRST + '","a":"' + SECOND + '"}', "a"),
    ],
)
def test_no_shared_secret_or_legacy_fallback(monkeypatch, encoded, active):
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr(FIRST))
    monkeypatch.setattr(
        settings,
        "WORKFLOW_WEBHOOK_KEYS",
        SecretStr(encoded) if encoded is not None else None,
        raising=False,
    )
    monkeypatch.setattr(
        settings,
        "WORKFLOW_WEBHOOK_ACTIVE_KEY_ID",
        active,
        raising=False,
    )
    with pytest.raises(WebhookCryptoError):
        ensure_credential_crypto()


@pytest.mark.parametrize(
    "damaged",
    [
        "",
        "old-token",
        "wh1:missing:token",
        "wh2:first:token",
        "wh1:legacy:token",
        "wh1:first:☃",
        "x" * 8193,
    ],
)
def test_malformed_tokens_use_safe_errors(monkeypatch, damaged):
    ring(monkeypatch)
    with pytest.raises(WebhookCryptoError) as failure:
        open_credential(**scope(), encrypted=damaged)
    if damaged:
        assert damaged not in str(failure.value)
    assert FIRST not in str(failure.value)


def test_authenticated_token_tampering_is_rejected(monkeypatch):
    ring(monkeypatch)
    owner = scope()
    encrypted = seal_credential(**owner, secret=SECRET)
    damaged = encrypted[:35] + ("A" if encrypted[35] != "A" else "B")
    damaged += encrypted[36:]
    with pytest.raises(WebhookCryptoError):
        open_credential(**owner, encrypted=damaged)


@pytest.mark.parametrize("invalid", ["", "short", "☃" * 40, "a" * 513])
def test_signing_credential_bounds_are_checked(monkeypatch, invalid):
    ring(monkeypatch)
    with pytest.raises(WebhookCryptoError):
        seal_credential(**scope(), secret=SecretStr(invalid))
