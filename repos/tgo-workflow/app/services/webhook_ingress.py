"""Authenticate raw bytes, then freeze one durable source before queue I/O."""

from datetime import datetime, timezone
from collections.abc import Mapping
from uuid import UUID, uuid5

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integrations.webhook_authority_client import verify_webhook_authority
from app.models.workflow_webhook import WorkflowWebhook
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.schemas.webhook_delivery import (
    WebhookAccepted,
    VerifiedWebhookDelivery,
)
from app.services.webhook_crypto import WebhookCryptoError, open_credential
from app.services.webhook_payloads import freeze_payload, matches_payload
from app.services.webhook_protocol import verify_delivery, WebhookProtocolError
from app.services.webhook_rate_limit import admit_webhook
from app.services.webhook_state import (
    START_WINDOW,
    authority,
    binding_error,
    event_key,
    lock_hook,
    payload_scope,
)


def accepted(
    row: WorkflowWebhookDelivery, *, duplicate: bool
) -> WebhookAccepted:
    return WebhookAccepted.model_validate(
        dict(
            delivery_id=row.id,
            status=row.status,
            execution_id=row.execution_id,
            deduplicated=duplicate,
        )
    )


def _replay(
    row: WorkflowWebhookDelivery, event: VerifiedWebhookDelivery
) -> WebhookAccepted:
    try:
        same = row.hook_id == str(event.hook_id) and matches_payload(
            event.hook_id,
            row.event_key,
            event.data,
            row.payload_key_id,
            row.payload_receipt,
        )
    except WebhookCryptoError:
        raise HTTPException(503, "原外部通知回执所需保管密钥不可用。") from None
    if not same:
        raise HTTPException(409, "外部通知编号已用于不同内容。")
    return accepted(row, duplicate=True)


async def accept_delivery(
    db: AsyncSession,
    hook_id: UUID,
    *,
    body: bytes,
    headers: Mapping[str, str | None],
    content_type: str | None,
) -> WebhookAccepted:
    if not settings.WEBHOOK_EXECUTION_ENABLED:
        raise HTTPException(503, "外部通知接收尚未开放。")
    hook = await db.get(WorkflowWebhook, str(hook_id))
    if hook is None:
        raise HTTPException(404, "外部通知来源不存在。")
    try:
        secret = open_credential(
            hook_id=hook_id,
            credential_revision=hook.credential_revision,
            key_id=hook.key_id,
            encrypted=hook.credential_ciphertext,
        )
        event = verify_delivery(
            hook_id=hook_id,
            credential_revision=hook.credential_revision,
            key_id=hook.key_id,
            secret=secret,
            body=body,
            headers=headers,
            content_type=content_type,
        )
    except WebhookCryptoError:
        raise HTTPException(503, "外部通知签名凭证暂时无法核验。") from None
    except WebhookProtocolError as error:
        raise HTTPException(error.status_code, str(error)) from None
    fingerprint = event_key(hook_id, event.event_id)
    await admit_webhook(str(hook_id), fingerprint)
    query = authority(hook)
    await db.commit()
    try:
        await verify_webhook_authority(query)
    except PermissionError:
        raise HTTPException(403, "外部通知来源授权已失效。") from None
    except RuntimeError:
        raise HTTPException(503, "外部通知来源权限暂时无法核验。") from None
    hook = await lock_hook(db, str(hook_id))
    if (
        hook is None
        or authority(hook).model_dump(exclude={"check_id"})
        != query.model_dump(exclude={"check_id"})
        or await binding_error(db, hook)
    ):
        raise HTTPException(409, "外部通知来源已停用、修订或失去固定发布版本。")
    identifier = str(uuid5(hook_id, "tgo-webhook-delivery:v1:" + fingerprint))
    prior = await db.get(WorkflowWebhookDelivery, identifier)
    if prior is not None:
        return _replay(prior, event)
    now = datetime.now(timezone.utc)
    row = WorkflowWebhookDelivery(
        id=identifier,
        hook_id=str(hook_id),
        project_id=hook.project_id,
        workflow_id=hook.workflow_id,
        actor_staff_id=hook.actor_staff_id,
        binding_revision=hook.revision,
        credential_revision=hook.credential_revision,
        event_key=fingerprint,
        release_id=hook.release_id,
        release_digest=hook.release_digest,
        status="pending",
        attempts=0,
        next_attempt_at=now,
        expires_at=now + START_WINDOW,
        created_at=now,
        updated_at=now,
    )
    try:
        frozen = freeze_payload(payload_scope(row), event.data)
    except WebhookCryptoError:
        raise HTTPException(503, "外部通知输入暂时无法安全保管。") from None
    row.payload_key_id, row.payload_receipt = frozen.key_id, frozen.receipt
    row.payload_ciphertext = frozen.ciphertext
    db.add(row)
    result = accepted(row, duplicate=False)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        prior = await db.get(WorkflowWebhookDelivery, identifier)
        if prior is None:
            raise HTTPException(409, "外部通知受理发生冲突，请稍后重试。") from None
        return _replay(prior, event)
    return result
