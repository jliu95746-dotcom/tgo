"""External webhook authentication rejects ambiguous data before dispatch."""

import hashlib
import hmac
import json
from uuid import UUID

from pydantic import SecretStr
import pytest

from app.services.webhook_protocol import (
    MAX_BODY_BYTES,
    WebhookProtocolError,
    verify_delivery,
    signing_message,
)

HOOK = UUID("11111111-2222-4333-8444-555555555555")
OTHER = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
KEY = "synthetic-webhook-secret-for-tests-only-1234"
NOW = 1_800_000_000


def signed(body=b'{"data":{"sku":"LV-T01"}}', **changes):
    headers = {
        "timestamp": str(NOW),
        "event_id": "source-001",
        "key_id": "key-v1",
    }
    headers.update(changes)
    # Independent sender encoding: do not reuse the receiver's helper.
    message = (
        f"TGO-WEBHOOK-V1\n{HOOK}\n1\n{headers['key_id']}\n"
        f"{headers['timestamp']}\n{headers['event_id']}\n"
    ).encode() + body
    headers["signature"] = (
        "sha256="
        + hmac.new(
            KEY.encode(),
            message,
            hashlib.sha256,
        ).hexdigest()
    )
    return headers


def verify(body=b'{"data":{"sku":"LV-T01"}}', *, headers=None, **changes):
    arguments = {
        "hook_id": HOOK,
        "credential_revision": 1,
        "key_id": "key-v1",
        "secret": SecretStr(KEY),
        "body": body,
        "content_type": "application/json",
        "now": NOW,
        "headers": signed(body) if headers is None else headers,
    }
    arguments.update(changes)
    return verify_delivery(**arguments)


def test_independent_sender_and_domain_separated_format():
    body = '{"data":{"问题":"这款包是什么材质？"}}'.encode()
    result = verify(body)
    assert result.hook_id == HOOK and result.credential_revision == 1
    assert result.event_id == "source-001" and result.key_id == "key-v1"
    assert result.data == {"问题": "这款包是什么材质？"}
    rendered = result.model_dump_json()
    assert KEY not in rendered and "signature" not in rendered
    assert signing_message(
        HOOK, 1, "key-v1", str(NOW), "source-001", body
    ) == (
        f"TGO-WEBHOOK-V1\n{HOOK}\n1\nkey-v1\n{NOW}\nsource-001\n".encode()
        + body
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"hook_id": OTHER},
        {"credential_revision": 2},
        {"key_id": "key-v2"},
        {"secret": SecretStr("different-synthetic-webhook-key-1234")},
    ],
)
def test_signature_is_bound_to_registered_hook_revision_and_key(changes):
    with pytest.raises(WebhookProtocolError) as error:
        verify(**changes)
    assert error.value.status_code == 403


def test_byte_change_requires_new_signature_even_if_json_is_equivalent():
    body = b'{"data":{"sku":"LV-T01"}}'
    with pytest.raises(WebhookProtocolError) as error:
        verify(body + b" ", headers=signed(body))
    assert error.value.code == "webhook_signature_invalid"


@pytest.mark.parametrize("offset", [-301, 301])
def test_old_and_future_signatures_expire(offset):
    with pytest.raises(WebhookProtocolError) as error:
        verify(headers=signed(timestamp=str(NOW + offset)))
    assert error.value.code == "webhook_signature_expired"


@pytest.mark.parametrize("offset", [-300, 300])
def test_exact_clock_window_is_accepted(offset):
    assert verify(headers=signed(timestamp=str(NOW + offset))).event_id


@pytest.mark.parametrize(
    "name,value",
    [
        ("timestamp", None),
        ("timestamp", "01"),
        ("timestamp", "１800000000"),
        ("timestamp", "1800000000\n"),
        ("event_id", "bad\nidentifier"),
        ("event_id", "x" * 129),
        ("key_id", "bad\nkey"),
        ("signature", "sha256=" + "A" * 64),
        ("signature", None),
    ],
)
def test_malformed_headers_are_rejected(name, value):
    headers = signed()
    headers[name] = value
    with pytest.raises(WebhookProtocolError) as error:
        verify(headers=headers)
    assert error.value.code == "webhook_headers_invalid"


def test_unknown_header_fields_cannot_smuggle_context_or_secrets():
    headers = signed()
    headers["actor_staff_id"] = "synthetic-private-value"
    with pytest.raises(WebhookProtocolError) as error:
        verify(headers=headers)
    assert "synthetic-private-value" not in str(error.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"secret": None},
        {"secret": SecretStr("short")},
        {"credential_revision": 0},
        {"credential_revision": True},
        {"key_id": ""},
        {"now": -1},
        {"secret": SecretStr("x" * 513)},
        {"secret": SecretStr("密" * 32)},
        {"key_id": None},
        {"now": 1.5},
        {"hook_id": str(HOOK)},
    ],
)
def test_invalid_server_configuration_never_falls_back_to_unsigned(changes):
    with pytest.raises(WebhookProtocolError) as error:
        verify(**changes)
    assert error.value.code == "webhook_configuration_invalid"
    assert error.value.status_code == 503


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "Application/JSON; charset=UTF-8",
        ' application/json ; charset = "utf-8" ',
    ],
)
def test_utf8_json_media_type(content_type):
    assert verify(content_type=content_type).event_id


@pytest.mark.parametrize(
    "content_type",
    [
        "text/plain",
        "application/json; charset=utf-16",
        "application/json; charset=utf-8; charset=utf-8",
        "application/json; foo=x",
        None,
    ],
)
def test_other_encodings_or_parameters_are_rejected(content_type):
    with pytest.raises(WebhookProtocolError) as error:
        verify(content_type=content_type)
    assert error.value.status_code == 415


def test_oversized_bytes_are_rejected_before_json_parsing():
    body = b"x" * (MAX_BODY_BYTES + 1)
    with pytest.raises(WebhookProtocolError) as error:
        verify(body)
    assert error.value.status_code == 413


def test_exact_body_budget_is_accepted_without_truncating_signed_bytes():
    body = b'{"data":{}}'
    body += b" " * (MAX_BODY_BYTES - len(body))
    assert verify(body).data == {}


@pytest.mark.parametrize(
    "body",
    [
        b"\xff",
        b'\xef\xbb\xbf{"data":{}}',
        b"{",
        b"null",
        b"[]",
        b"{}",
        b'{"data":true}',
        b'{"data":{},"data":{"x":1}}',
        b'{"data":{"x":1,"x":2}}',
        b'{"data":{"x":NaN}}',
        b'{"data":{"x":Infinity}}',
        b'{"data":{"x":1e999}}',
        b'{"data":{},"project_id":"foreign"}',
        b'{"data":{"project_id":"foreign"}}',
        b'{"data":{"hook_id":"foreign"}}',
        b'{"data":{"credential_revision":99}}',
        b'{"data":{"binding_revision":99}}',
        b'{"data":{"key_id":"foreign"}}',
        b'{"data":{"nested":[{"Source_Binding":{}}]}}',
        b'{"data":{"__proto__":{}}}',
        b'{"data":{"constructor":{}}}',
        b'{"data":{"text":"\\ud800"}}',
        b'{"data":{"\\udfff":1}}',
        b'{"data":{"text":"\\u0000"}}',
        b'{"data":{"\\u0000":1}}',
    ],
)
def test_signed_but_ambiguous_or_reserved_payloads_are_rejected(body):
    with pytest.raises(WebhookProtocolError) as error:
        verify(body)
    assert error.value.code == "webhook_payload_invalid"
    assert error.value.status_code == 422
    assert "foreign" not in str(error.value)


def test_signed_structure_budgets_are_enforced_without_echoing_values():
    deep = {"value": "synthetic-private-value"}
    for _ in range(12):
        deep = {"nested": deep}
    for data in (
        deep,
        {"x": "synthetic-private-value" * 300},
        {"x": [0] * 1025},
        {str(index): 0 for index in range(129)},
        {"": 1},
        {"k" * 129: 1},
    ):
        with pytest.raises(WebhookProtocolError) as error:
            verify(json.dumps({"data": data}).encode())
        assert "synthetic-private-value" not in str(error.value)
        assert error.value.status_code == 422


def test_empty_and_finite_json_values_are_supported():
    assert verify(b'{"data":{}}').data == {}
    assert verify(b'{"data":{"values":[0,1.5,true,null]}}').data == {
        "values": [0, 1.5, True, None],
    }
    assert verify(b'{"data":{"text":"\\ud83d\\ude00"}}').data["text"] == "😀"
