"""Event retries retain their original verification key across rotation."""

import json

import pytest
from pydantic import SecretStr

from app.config import settings
from app.services.event_keyring import EventKeyringError, parse_event_keyring
from app.services.event_receipts import new_event_receipt, saved_event_receipt
from app.services.execution_snapshot import input_receipt_hmac


OLD = "old-receipt-material-for-tests-0123456789"
NEW = "new-receipt-material-for-tests-0123456789"


def configure(monkeypatch, keys=None, active=None):
    monkeypatch.setattr(
        settings,
        "WORKFLOW_EVENT_RECEIPT_KEYS",
        SecretStr(json.dumps(keys)) if keys is not None else None,
    )
    monkeypatch.setattr(
        settings, "WORKFLOW_EVENT_RECEIPT_ACTIVE_KEY_ID", active
    )


def test_legacy_receipt_format_is_preserved_until_keyring_is_enabled(
    monkeypatch,
):
    configure(monkeypatch)
    key_id, receipt = new_event_receipt({"data": {}}, OLD, "run")
    assert key_id is None
    assert receipt == input_receipt_hmac({"data": {}}, OLD, "run")


def test_rotation_verifies_saved_key_and_ignores_changed_internal_secret(
    monkeypatch,
):
    configure(monkeypatch, {"receipt1": OLD}, "receipt1")
    key_id, receipt = new_event_receipt(
        {"data": {"text": "片段"}}, "internal-before", "run"
    )
    configure(monkeypatch, {"receipt1": OLD, "receipt2": NEW}, "receipt2")
    assert (
        saved_event_receipt(
            {"data": {"text": "片段"}}, "internal-after", "run", key_id
        )
        == receipt
    )
    next_id, next_receipt = new_event_receipt(
        {"data": {"text": "片段"}}, "internal-after", "run"
    )
    assert next_id == "receipt2" and next_receipt != receipt
    assert (
        saved_event_receipt(
            {"data": {"text": "changed"}}, "internal-after", "run", key_id
        )
        != receipt
    )


def test_legacy_import_and_missing_key_fail_without_silent_fallback(
    monkeypatch,
):
    legacy = input_receipt_hmac({"data": {}}, OLD, "run")
    configure(monkeypatch, {"legacy": OLD, "receipt2": NEW}, "receipt2")
    assert saved_event_receipt({"data": {}}, NEW, "run", None) == legacy
    configure(monkeypatch, {"receipt2": NEW}, "receipt2")
    for missing in (None, "retired"):
        with pytest.raises(EventKeyringError) as error:
            saved_event_receipt({"data": {}}, OLD, "run", missing)
        assert OLD not in str(error.value) and NEW not in str(error.value)


@pytest.mark.parametrize(
    "raw,active",
    [
        ('{"k":"private-short"}', "k"),
        ('{"k":"' + OLD + '","k":"' + NEW + '"}', "k"),
        ("{}", "k"),
        ("[]", "k"),
        ('{"bad:key":"' + OLD + '"}', "bad:key"),
        ('{"k":123}', "k"),
        ('{"k":"' + OLD + '"}', None),
        (None, "k"),
        ('{"legacy":"' + OLD + '"}', "legacy"),
    ],
)
def test_invalid_key_config_never_falls_back_or_exposes_material(raw, active):
    with pytest.raises(EventKeyringError) as error:
        parse_event_keyring(
            SecretStr(raw) if raw is not None else None, active, OLD
        )
    assert "private-short" not in str(error.value) and OLD not in str(
        error.value
    )


def test_keyring_repr_hides_every_key():
    ring = parse_event_keyring(SecretStr(json.dumps({"k": OLD})), "k", NEW)
    assert OLD not in repr(ring) and NEW not in repr(ring)
