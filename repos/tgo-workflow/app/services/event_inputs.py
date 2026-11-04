"""Freeze event input separately from the metadata needed to revoke it."""

import base64
import hashlib
import hmac
import json

from cryptography.fernet import Fernet, InvalidToken
from pydantic import JsonValue

from app.config import settings
from app.models.execution import WorkflowExecution
from app.schemas.event_trigger import WorkflowEventSourceBinding
from app.services.event_keyring import (
    EventKeyring,
    EventKeyringError,
    parse_event_keyring,
)
from app.services.execution_snapshot import copy_json_object
from app.services.event_identity import event_execution_id
from app.services.event_receipts import saved_event_receipt

MAX_EVENT_INPUT_BYTES = 1024 * 1024
PAYLOAD_FIELD = "_event_payload"


class EventInputKeyUnavailable(RuntimeError):
    """Keep the original pending run when a retained key is unavailable."""


def ensure_event_input_keys() -> None:
    ring = _keyring()
    _cipher(ring, ring.active_id or "legacy")


def _keyring() -> EventKeyring:
    fallback = (
        settings.SECRET_KEY.get_secret_value() if settings.SECRET_KEY else ""
    )
    try:
        ring = parse_event_keyring(
            settings.WORKFLOW_EVENT_INPUT_KEYS,
            settings.WORKFLOW_EVENT_INPUT_ACTIVE_KEY_ID,
            fallback,
        )
        if not ring.configured and len(fallback.encode("utf-8")) < 32:
            raise EventKeyringError()
        return ring
    except EventKeyringError:
        raise EventInputKeyUnavailable(
            "事件正文保管密钥未配置或无效。"
        ) from None


def _cipher(ring: EventKeyring, key_id: str) -> Fernet:
    try:
        secret = ring.secret(None if key_id == "legacy" else key_id)
        material = (
            b"tgo-event-input:v1\x00"
            + key_id.encode("ascii")
            + b"\x00"
            + secret.encode("utf-8")
        )
        return Fernet(
            base64.urlsafe_b64encode(hashlib.sha256(material).digest())
        )
    except (EventKeyringError, UnicodeError):
        raise EventInputKeyUnavailable(
            "事件正文所需历史保管密钥不可用。"
        ) from None


def _source(inputs: dict[str, JsonValue]) -> dict[str, JsonValue] | None:
    if "_event_source" not in inputs:
        return None
    try:
        return WorkflowEventSourceBinding.model_validate(
            inputs["_event_source"],
        ).model_dump(mode="json")
    except ValueError:
        raise ValueError("事件正文来源绑定无效。") from None


def _scope(run: WorkflowExecution, source: JsonValue) -> dict[str, JsonValue]:
    if (
        run.source_channel != "internal_event"
        or run.execution_mode != "production"
        or not run.id
        or not run.project_id
        or not run.workflow_id
        or not run.source_message_id
        or not run.release_id
        or not run.definition_digest
        or not run.workflow_version
        or not run.invocation_input_hmac
    ):
        raise ValueError("事件正文归属信息不完整。")
    if source is None and run.visitor_session_id is not None:
        raise ValueError("业务事件缺少来源绑定。")
    if (
        isinstance(source, dict)
        and source.get("session_id") != run.visitor_session_id
    ):
        raise ValueError("事件正文会话绑定不一致。")
    return {
        "project_id": run.project_id,
        "workflow_id": run.workflow_id,
        "execution_id": run.id,
        "event_id": run.source_message_id,
        "release_id": run.release_id,
        "definition_digest": run.definition_digest,
        "workflow_version": run.workflow_version,
        "source_channel": run.source_channel,
        "execution_mode": run.execution_mode,
        "visitor_session_id": run.visitor_session_id,
        "receipt_key_id": run.event_receipt_key_id,
        "receipt_hmac": run.invocation_input_hmac,
        "workflow_lineage": list(run.workflow_lineage or [run.workflow_id]),
        "source_binding": source,
    }


def _plain_inputs(inputs: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if set(inputs) - {"data", "_event_source"} or not isinstance(
        inputs.get("data"), dict
    ):
        raise ValueError("事件冻结输入格式无效。")
    result = copy_json_object(inputs)
    _source(result)
    return result


def _legacy_inputs(
    run: WorkflowExecution, inputs: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    frozen = _plain_inputs(inputs)
    if (
        not run.source_message_id
        or not run.invocation_input_hmac
        or run.source_channel != "internal_event"
        or run.execution_mode != "production"
        or run.id
        != event_execution_id(
            run.project_id, run.workflow_id, run.source_message_id
        )
        or not isinstance(run.definition_snapshot, dict)
    ):
        raise ValueError("旧事件输入缺少原始调用凭据。")
    nodes = run.definition_snapshot.get("nodes")
    triggers = (
        [
            node
            for node in nodes
            if isinstance(node, dict) and node.get("type") == "event"
        ]
        if isinstance(nodes, list)
        else []
    )
    data = triggers[0].get("data") if len(triggers) == 1 else None
    if not isinstance(data, dict) or not isinstance(
        data.get("event_type"), str
    ):
        raise ValueError("旧事件输入缺少原触发类型。")
    receipt: dict[str, JsonValue] = {
        "event_type": data["event_type"],
        "data": frozen["data"],
    }
    source = _source(frozen)
    if source is not None:
        if source.get("session_id") != run.visitor_session_id:
            raise ValueError("旧事件输入的会话绑定不一致。")
        receipt["source_binding"] = source
    elif run.visitor_session_id is not None:
        raise ValueError("旧事件输入缺少来源绑定。")
    variants = [] if source is not None else [receipt]
    if run.release_id and run.definition_digest:
        variants.append(
            {
                **receipt,
                "expected_release_id": run.release_id,
                "expected_release_digest": run.definition_digest,
            }
        )
    try:
        fallback = (
            settings.SECRET_KEY.get_secret_value()
            if settings.SECRET_KEY
            else ""
        )
        if any(
            hmac.compare_digest(
                run.invocation_input_hmac,
                saved_event_receipt(
                    value,
                    fallback,
                    run.id,
                    run.event_receipt_key_id,
                ),
            )
            for value in variants
        ):
            return frozen
    except (EventKeyringError, ValueError):
        raise EventInputKeyUnavailable(
            "旧事件输入所需历史回执密钥不可用。"
        ) from None
    raise ValueError("旧事件输入与原始调用凭据不一致。")


def seal_event_inputs(
    run: WorkflowExecution,
    inputs: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    frozen = _plain_inputs(inputs)
    source = _source(frozen)
    payload = {"scope": _scope(run, source), "inputs": frozen}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_EVENT_INPUT_BYTES:
        raise ValueError("事件冻结输入超过保管上限。")
    ring = _keyring()
    key_id = ring.active_id or "legacy"
    result: dict[str, JsonValue] = {
        PAYLOAD_FIELD: {
            "capture_status": "encrypted",
            "key_id": key_id,
            "ciphertext": _cipher(ring, key_id)
            .encrypt(encoded)
            .decode("ascii"),
        },
    }
    if source is not None:
        result["_event_source"] = source
    return result


def frozen_event_inputs(run: WorkflowExecution) -> dict[str, JsonValue]:
    """Read frozen input without granting source authority."""
    inputs = run.input
    if not isinstance(inputs, dict):
        raise ValueError("事件冻结输入已清理或无效。")
    if PAYLOAD_FIELD not in inputs:
        # Old runs need their original proof. New ingress always seals.
        return _legacy_inputs(run, inputs)
    if set(inputs) - {PAYLOAD_FIELD, "_event_source"}:
        raise ValueError("事件密文封套无效。")
    envelope = inputs[PAYLOAD_FIELD]
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"capture_status", "key_id", "ciphertext"}
        or envelope.get("capture_status") != "encrypted"
    ):
        raise ValueError("事件密文封套无效。")
    key_id, ciphertext = envelope.get("key_id"), envelope.get("ciphertext")
    if (
        not isinstance(key_id, str)
        or not 1 <= len(key_id) <= 32
        or not isinstance(ciphertext, str)
        or len(ciphertext) > MAX_EVENT_INPUT_BYTES * 2
    ):
        raise ValueError("事件密文封套无效。")
    source = _source(inputs)
    expected_scope = _scope(run, source)
    try:
        clear = _cipher(_keyring(), key_id).decrypt(ciphertext.encode("ascii"))
        if len(clear) > MAX_EVENT_INPUT_BYTES:
            raise ValueError()
        decoded = json.loads(clear)
        if (
            not isinstance(decoded, dict)
            or set(decoded) != {"scope", "inputs"}
            or decoded["scope"] != expected_scope
            or not isinstance(decoded["inputs"], dict)
        ):
            raise ValueError()
        frozen = _plain_inputs(decoded["inputs"])
        if _source(frozen) != source:
            raise ValueError()
        return frozen
    except InvalidToken:
        raise EventInputKeyUnavailable(
            "事件冻结输入暂时无法解密，请核对历史保管密钥。"
        ) from None
    except (UnicodeError, ValueError):
        raise ValueError("事件冻结输入无法核验。") from None
