"""Encrypt continuation state for the exact run and approval that own it."""

import base64
from datetime import UTC
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from pydantic import JsonValue

from app.config import settings
from app.engine.checkpoint import ExecutionCheckpoint
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from app.services.event_keyring import (
    EventKeyring,
    EventKeyringError,
    parse_event_keyring,
)

MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024
VERIFIED_RESUME_CAPTURE = "encrypted_verified_resume"
EXTERNAL_SOURCES = frozenset({"internal_event", "external_webhook"})


class CheckpointCryptoError(ValueError):
    """A safe error; never include plaintext, ciphertext, or key material."""


class ApprovalCheckpointKeyUnavailable(CheckpointCryptoError):
    """The original approved run must wait for retained key configuration."""


def _keyring() -> EventKeyring:
    fallback = settings.SECRET_KEY.get_secret_value() if settings.SECRET_KEY else ""
    try:
        ring = parse_event_keyring(
            settings.WORKFLOW_APPROVAL_KEYS,
            settings.WORKFLOW_APPROVAL_ACTIVE_KEY_ID,
            fallback,
        )
        if not ring.configured and len(fallback.encode("utf-8")) < 32:
            raise EventKeyringError()
        return ring
    except EventKeyringError:
        raise ApprovalCheckpointKeyUnavailable("审批检查点保管密钥未配置或无效。") from None


def _cipher(ring: EventKeyring, key_id: str) -> Fernet:
    try:
        secret = ring.secret(None if key_id == "legacy" else key_id)
        material = (
            b"tgo-approval-checkpoint:v1\x00"
            + key_id.encode("ascii")
            + b"\x00"
            + secret.encode("utf-8")
        )
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))
    except (EventKeyringError, UnicodeError):
        raise ApprovalCheckpointKeyUnavailable("审批检查点所需历史保管密钥不可用。") from None


def _scope(
    run: WorkflowExecution,
    request: WorkflowApprovalRequest,
) -> dict[str, JsonValue]:
    if (
        not request.id
        or not request.node_id
        or not request.approver_staff_id
        or request.project_id != run.project_id
        or request.execution_id != run.id
        or not run.definition_digest
        or request.expires_at is None
    ):
        raise CheckpointCryptoError("审批检查点归属信息不完整。")
    expiry = request.expires_at
    expiry = (
        expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry.astimezone(UTC)
    )
    return {
        "project_id": run.project_id,
        "workflow_id": run.workflow_id,
        "execution_id": run.id,
        "approval_id": request.id,
        "node_id": request.node_id,
        "approver_staff_id": request.approver_staff_id,
        "expires_at": expiry.isoformat(timespec="microseconds"),
        "summary": request.summary,
        "definition_digest": run.definition_digest,
        "workflow_version": run.workflow_version,
        "release_id": run.release_id,
        "execution_mode": run.execution_mode,
        "source_channel": run.source_channel,
        "source_message_id": run.source_message_id,
        "visitor_session_id": run.visitor_session_id,
        "employee_agent_id": run.employee_agent_id,
        "business_request_id": run.business_request_id,
        "workflow_lineage": list(run.workflow_lineage or [run.workflow_id]),
    }


def _verify_identity(
    checkpoint: ExecutionCheckpoint,
    run: WorkflowExecution,
    request: WorkflowApprovalRequest,
) -> None:
    if (
        checkpoint.project_id != run.project_id
        or checkpoint.execution_id != run.id
        or checkpoint.definition_digest != run.definition_digest
        or checkpoint.workflow_lineage
        != list(run.workflow_lineage or [run.workflow_id])
        or not checkpoint.completed_nodes
        or checkpoint.completed_nodes[-1] != request.node_id
    ):
        raise CheckpointCryptoError("审批检查点与原运行不一致。")


def seal_checkpoint(
    run: WorkflowExecution,
    request: WorkflowApprovalRequest,
    checkpoint: ExecutionCheckpoint,
) -> dict[str, JsonValue]:
    _verify_identity(checkpoint, run, request)
    payload = {
        "scope": _scope(run, request),
        "checkpoint": checkpoint.model_dump(mode="json"),
    }
    try:
        plaintext = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise CheckpointCryptoError("审批检查点不是有效的 JSON 数据。") from None
    if len(plaintext) > MAX_CHECKPOINT_BYTES:
        raise CheckpointCryptoError("审批检查点超过允许大小。")
    ring = _keyring()
    key_id = ring.active_id or "legacy"
    return {
        "capture_status": "encrypted",
        "key_id": key_id,
        "ciphertext": _cipher(ring, key_id).encrypt(plaintext).decode("ascii"),
    }


def open_checkpoint(
    run: WorkflowExecution,
    request: WorkflowApprovalRequest,
) -> ExecutionCheckpoint:
    saved = request.checkpoint
    if not isinstance(saved, dict):
        raise CheckpointCryptoError("审批检查点格式无效。")
    if saved.get("capture_status") not in ("encrypted", VERIFIED_RESUME_CAPTURE):
        # These sources had no supported pending approvals before encryption.
        if run.source_channel in EXTERNAL_SOURCES:
            raise CheckpointCryptoError("外部来源审批检查点必须加密。")
        try:
            legacy = ExecutionCheckpoint.model_validate(saved)
        except ValueError:
            raise CheckpointCryptoError("历史审批检查点无效。") from None
        _verify_identity(legacy, run, request)
        return legacy
    try:
        key_id, encrypted = saved.get("key_id"), saved.get("ciphertext")
        if (
            set(saved) != {"capture_status", "key_id", "ciphertext"}
            or not isinstance(key_id, str)
            or not isinstance(encrypted, str)
            or not 1 <= len(key_id) <= 32
            or len(encrypted) > 2 * MAX_CHECKPOINT_BYTES
        ):
            raise ValueError()
        plaintext = _cipher(_keyring(), key_id).decrypt(encrypted.encode("ascii"))
        if len(plaintext) > MAX_CHECKPOINT_BYTES:
            raise ValueError()
        payload = json.loads(plaintext)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"scope", "checkpoint"}
            or payload["scope"] != _scope(run, request)
        ):
            raise ValueError()
        checkpoint = ExecutionCheckpoint.model_validate(payload["checkpoint"])
    except CheckpointCryptoError:
        raise
    except (InvalidToken, ValueError, TypeError, UnicodeError):
        raise CheckpointCryptoError("审批检查点无法解密或归属信息已变化。") from None
    _verify_identity(checkpoint, run, request)
    return checkpoint
