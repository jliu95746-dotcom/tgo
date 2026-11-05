"""Scope-bound encryption; never persist a question or a key in a receipt."""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.config import settings
from app.models.execution import WorkflowExecution
from app.schemas.ai_usage import WorkflowAICacheCommand, WorkflowAIResult
from app.services.event_keyring import EventKeyringError, parse_event_keyring

MAX_RESULT_BYTES = 512 * 1024


class SavedAIResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: WorkflowAIResult


def utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _secret(key_id: str) -> str:
    fallback = (
        settings.SECRET_KEY.get_secret_value() if settings.SECRET_KEY else ""
    )
    try:
        ring = parse_event_keyring(
            settings.WORKFLOW_AI_RESULT_KEYS,
            settings.WORKFLOW_AI_RESULT_ACTIVE_KEY_ID,
            fallback,
        )
        value = ring.secret(None if key_id == "legacy" else key_id)
        if len(value.encode()) < 32:
            raise EventKeyringError()
        return value
    except EventKeyringError:
        raise HTTPException(503, "原回答所需的保管密钥不可用。") from None


def active_key_id() -> str:
    fallback = (
        settings.SECRET_KEY.get_secret_value() if settings.SECRET_KEY else ""
    )
    try:
        ring = parse_event_keyring(
            settings.WORKFLOW_AI_RESULT_KEYS,
            settings.WORKFLOW_AI_RESULT_ACTIVE_KEY_ID,
            fallback,
        )
        key_id = ring.active_id or "legacy"
        _secret(key_id)
        return key_id
    except EventKeyringError:
        raise HTTPException(503, "回答恢复保管密钥尚未配置。") from None


def _cipher(key_id: str) -> Fernet:
    raw = b"tgo-workflow-ai-result:v1\x00" + key_id.encode("ascii")
    raw += b"\x00" + _secret(key_id).encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))


def fingerprint(
    run: WorkflowExecution,
    command: WorkflowAICacheCommand,
    claim_id: str,
    expires_at: datetime,
    key_id: str,
) -> str:
    lineage: list[JsonValue] | None = None
    if run.workflow_lineage is not None:
        lineage = list(run.workflow_lineage)
    scope: dict[str, JsonValue] = {
        "command": command.model_dump(mode="json"),
        "project_id": run.project_id,
        "workflow_id": run.workflow_id,
        "execution_id": run.id,
        "definition_digest": run.definition_digest,
        "workflow_version": run.workflow_version,
        "release_id": run.release_id,
        "execution_mode": run.execution_mode,
        "employee_agent_id": run.employee_agent_id,
        "workflow_lineage": lineage,
        "owner": run.ai_usage_owner,
        "visitor_session_id": run.visitor_session_id,
        "business_request_id": run.business_request_id,
        "source_message_id": run.source_message_id,
        "source_channel": run.source_channel,
        "parent_execution_id": run.parent_execution_id,
        "claim_id": claim_id,
        "expires_at": utc(expires_at).isoformat(),
    }
    encoded = json.dumps(
        scope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hmac.new(
        _secret(key_id).encode(),
        b"workflow-answer-scope:v1\x00" + encoded,
        hashlib.sha256,
    ).hexdigest()


def seal(result: WorkflowAIResult, digest: str, key_id: str) -> str:
    raw = SavedAIResult(scope=digest, result=result).model_dump_json().encode()
    if len(raw) > MAX_RESULT_BYTES:
        raise HTTPException(413, "原回答超过允许的恢复大小。")
    return _cipher(key_id).encrypt(raw).decode("ascii")


def unseal(ciphertext: str, digest: str, key_id: str) -> WorkflowAIResult:
    try:
        if len(ciphertext) > MAX_RESULT_BYTES * 2:
            raise ValueError()
        clear = _cipher(key_id).decrypt(ciphertext.encode("ascii"))
        if len(clear) > MAX_RESULT_BYTES:
            raise ValueError()
        saved = SavedAIResult.model_validate_json(clear)
        if not hmac.compare_digest(saved.scope, digest):
            raise ValueError()
        return saved.result
    except (ValueError, UnicodeError, InvalidToken):
        raise HTTPException(409, "原回答回执无法核验，请查看原运行记录。") from None
