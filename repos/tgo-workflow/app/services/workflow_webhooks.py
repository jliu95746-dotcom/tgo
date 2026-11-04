"""Serialize webhook registration and recoverable command facts."""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
from typing import Literal, cast
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import Workflow
from app.models.workflow_webhook import WorkflowWebhook, WorkflowWebhookCommand
from app.schemas.workflow_webhook import (
    WebhookChange,
    WebhookCredential,
    WebhookReceipt,
    WebhookSave,
    WebhookState,
    WebhookSummary,
)
from app.services.execution_snapshot import copy_json_object
from app.services.validation_service import ValidationService
from app.services.webhook_crypto import (
    WebhookCryptoError,
    ensure_credential_crypto,
    open_credential,
    seal_credential,
)
from app.services.workflow_releases import (
    production_definition,
    verify_execution_binding,
)
from app.config import settings
from app.integrations.webhook_authority_client import verify_webhook_authority
from app.services.webhook_state import authority
from app.services.webhook_management_views import published_context

Operation = Literal["save", "disable", "rotate"]
CONFLICT = "外部触发配置已有变化或请求编号已被使用，请读取原结果后重试。"


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _summary(row: WorkflowWebhook) -> WebhookSummary:
    result = WebhookSummary.model_validate(row)
    return result.model_copy(
        update={
            "created_at": _utc(result.created_at),
            "updated_at": _utc(result.updated_at),
        }
    )


async def _workflow(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    *,
    lock: bool = False,
) -> Workflow:
    query = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.project_id == str(project_id),
    )
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "工作流不存在。")
    return row


async def _webhook(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
) -> WorkflowWebhook | None:
    result = await db.scalars(
        select(WorkflowWebhook)
        .where(
            WorkflowWebhook.workflow_id == workflow_id,
            WorkflowWebhook.project_id == str(project_id),
        )
        .execution_options(populate_existing=True)
    )
    return result.one_or_none()


async def _command(
    db: AsyncSession,
    project_id: UUID,
    request_id: UUID,
) -> WorkflowWebhookCommand | None:
    return await db.get(
        WorkflowWebhookCommand,
        (str(project_id), str(request_id)),
        populate_existing=True,
    )


def _credential(row: WorkflowWebhook) -> SecretStr:
    return open_credential(
        hook_id=UUID(row.id),
        credential_revision=row.credential_revision,
        key_id=row.key_id,
        encrypted=row.credential_ciphertext,
    )


async def _receipt(
    db: AsyncSession,
    row: WorkflowWebhookCommand,
    *,
    replayed: bool,
) -> WebhookReceipt:
    if row.operation not in {"save", "disable", "rotate"}:
        raise HTTPException(503, "外部触发原请求回执无法核验。")
    try:
        snapshot = WebhookSummary.model_validate(row.result_snapshot)
        result = WebhookReceipt(
            request_id=UUID(row.request_id),
            operation=cast(Operation, row.operation),
            webhook=snapshot,
            replayed=replayed,
        )
    except (ValueError, ValidationError):
        raise HTTPException(503, "外部触发原请求回执无法核验。") from None
    if not row.credential_issued:
        return result
    current = await _webhook(db, row.workflow_id, UUID(row.project_id))
    if (
        current is None
        or current.id != str(snapshot.id)
        or current.credential_revision != snapshot.credential_revision
        or current.key_id != snapshot.key_id
    ):
        result.credential_status = "superseded"
        return result
    try:
        result.credential = WebhookCredential(secret=_credential(current))
        result.credential_status = "available"
    except WebhookCryptoError:
        result.credential_status = "unavailable"
    return result


def _digest(
    workflow_id: str,
    project_id: UUID,
    actor_id: UUID,
    operation: Operation,
    request: WebhookSave | WebhookChange,
) -> str:
    canonical = json.dumps(
        {
            "contract": "workflow.webhook.command.v1",
            "workflow_id": workflow_id,
            "project_id": str(project_id),
            "actor_staff_id": str(actor_id),
            "operation": operation,
            "request": request.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _replay(
    db: AsyncSession,
    row: WorkflowWebhookCommand,
    workflow_id: str,
    actor_id: UUID,
    digest: str,
) -> WebhookReceipt:
    if (
        row.workflow_id != workflow_id
        or row.actor_staff_id != str(actor_id)
        or not hmac.compare_digest(row.request_digest, digest)
    ):
        raise HTTPException(409, CONFLICT)
    return await _receipt(db, row, replayed=True)


async def get_command_receipt(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    actor_id: UUID,
    request_id: UUID,
) -> WebhookReceipt:
    await _workflow(db, workflow_id, project_id)
    row = await _command(db, project_id, request_id)
    if (
        row is None
        or row.workflow_id != workflow_id
        or row.actor_staff_id != str(actor_id)
    ):
        raise HTTPException(404, "外部触发原请求不存在。")
    return await _receipt(db, row, replayed=True)


async def get_webhook(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
) -> WebhookState:
    workflow = await _workflow(db, workflow_id, project_id)
    row = await _webhook(db, workflow_id, project_id)
    release = await published_context(db, workflow)
    current = bool(row is not None and release is not None
                   and row.release_id == release.release_id
                   and row.release_digest == release.definition_digest)
    available = True
    reason = (None if settings.WEBHOOK_EXECUTION_ENABLED else
              "外部触发接收与执行尚未开放，可先保存停用配置。")
    try:
        ensure_credential_crypto()
        if row is not None:
            _credential(row)
    except WebhookCryptoError:
        available = False
        reason = "外部触发凭证保管密钥未就绪，请联系管理员。"
    return WebhookState(
        webhook=_summary(row) if row else None,
        current_release=release,
        binding_current=current,
        registration_available=available,
        execution_available=settings.WEBHOOK_EXECUTION_ENABLED and available,
        reason=reason,
    )


def _issue_credential(row: WorkflowWebhook) -> None:
    if row.credential_revision > 2147483647:
        raise HTTPException(409, "外部触发凭证修订已达上限，请联系管理员。")
    row.key_id = uuid4().hex
    try:
        row.credential_ciphertext = seal_credential(
            hook_id=UUID(row.id),
            credential_revision=row.credential_revision,
            key_id=row.key_id,
            secret=SecretStr(secrets.token_urlsafe(48)),
        )
    except WebhookCryptoError:
        raise HTTPException(503, "外部触发凭证保管密钥未就绪。") from None


async def _apply_locked(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    actor_id: UUID,
    request: WebhookSave | WebhookChange,
    operation: Operation,
    digest: str,
) -> WebhookReceipt:
    await _workflow(db, workflow_id, project_id, lock=True)
    prior = await _command(db, project_id, request.request_id)
    if prior is not None:
        return await _replay(db, prior, workflow_id, actor_id, digest)
    row = await _webhook(db, workflow_id, project_id)
    if (row.revision if row else 0) != request.expected_revision:
        raise HTTPException(409, CONFLICT)
    now = datetime.now(timezone.utc)
    issued = False
    if isinstance(request, WebhookSave):
        if request.enabled and not settings.WEBHOOK_EXECUTION_ENABLED:
            raise HTTPException(409, "外部触发接收与执行尚未开放，配置不能启用。")
        published = await production_definition(
            db, workflow_id, str(project_id)
        )
        verify_execution_binding(
            published,
            request.expected_release_id,
            request.expected_release_digest,
        )
        try:
            errors = ValidationService.validate_workflow(
                published.definition,
                allow_webhook_preparation=True,
            )
            nodes = published.definition.get("nodes")
            triggers = (
                [
                    node
                    for node in nodes
                    if isinstance(node, dict) and node.get("type") == "webhook"
                ]
                if isinstance(nodes, list)
                else []
            )
        except (TypeError, ValueError, KeyError):
            raise HTTPException(409, "固定发布版本的外部触发配置无法核验。") from None
        if errors or len(triggers) != 1:
            raise HTTPException(409, "固定发布版本必须是配置有效的单一外部触发工作流。")
        if row is None:
            row = WorkflowWebhook(
                id=str(uuid4()),
                project_id=str(project_id),
                workflow_id=workflow_id,
                credential_revision=1,
                created_by=str(actor_id),
                created_at=now,
            )
            _issue_credential(row)
            issued = True
            db.add(row)
        row.release_id = published.release_id
        row.release_digest = request.expected_release_digest
        row.actor_staff_id = str(actor_id)
        row.enabled = request.enabled
    else:
        if row is None:
            raise HTTPException(404, "外部触发配置不存在。")
        row.enabled = False
        if operation == "rotate":
            row.credential_revision += 1
            _issue_credential(row)
            issued = True
    row.revision = request.expected_revision + 1
    row.updated_by, row.updated_at = str(actor_id), now
    if row.enabled:
        try:
            _credential(row)
            await verify_webhook_authority(authority(row))
        except WebhookCryptoError:
            raise HTTPException(503, "外部触发凭证保管密钥未就绪。") from None
        except PermissionError:
            raise HTTPException(403, "外部触发当前授权已失效。") from None
        except RuntimeError:
            raise HTTPException(503, "外部触发当前授权暂时无法核验。") from None
    await db.flush()
    from app.services.webhook_lifecycle import cancel_superseded
    await cancel_superseded(db, row, now)
    command = WorkflowWebhookCommand(
        project_id=str(project_id),
        request_id=str(request.request_id),
        workflow_id=workflow_id,
        actor_staff_id=str(actor_id),
        operation=operation,
        request_digest=digest,
        credential_issued=issued,
        result_snapshot=copy_json_object(
            _summary(row).model_dump(mode="json")
        ),
        created_at=now,
    )
    db.add(command)
    result = await _receipt(db, command, replayed=False)
    await db.commit()
    return result


async def _apply(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    actor_id: UUID,
    request: WebhookSave | WebhookChange,
    operation: Operation,
) -> WebhookReceipt:
    digest = _digest(workflow_id, project_id, actor_id, operation, request)
    try:
        return await _apply_locked(
            db, workflow_id, project_id, actor_id, request, operation, digest
        )
    except IntegrityError:
        await db.rollback()
        prior = await _command(db, project_id, request.request_id)
        if prior is None:
            raise HTTPException(409, CONFLICT) from None
        return await _replay(db, prior, workflow_id, actor_id, digest)
    except HTTPException:
        await db.rollback()
        raise


async def save_webhook(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    actor_id: UUID,
    request: WebhookSave,
) -> WebhookReceipt:
    return await _apply(db, workflow_id, project_id, actor_id, request, "save")


async def disable_webhook(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    actor_id: UUID,
    request: WebhookChange,
) -> WebhookReceipt:
    return await _apply(
        db, workflow_id, project_id, actor_id, request, "disable"
    )


async def rotate_webhook(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    actor_id: UUID,
    request: WebhookChange,
) -> WebhookReceipt:
    return await _apply(
        db, workflow_id, project_id, actor_id, request, "rotate"
    )
