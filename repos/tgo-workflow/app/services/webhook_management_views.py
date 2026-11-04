"""Verified publication context and tenant-owned source history."""

from uuid import UUID
from typing import cast

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import WorkflowExecution
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.schemas.webhook_delivery import DeliveryStatus
from app.schemas.webhook_history import (
    WebhookDeliveryList,
    WebhookDeliveryRecord,
)
from app.schemas.workflow_webhook import WebhookReleaseContext
from app.services.execution_snapshot import definition_digest
from app.services.validation_service import ValidationService
from app.services.webhook_state import execution_id, utc


async def published_context(
    db: AsyncSession,
    workflow: Workflow,
) -> WebhookReleaseContext | None:
    if workflow.status != "active" or workflow.published_version is None:
        return None
    release = await db.scalar(
        select(WorkflowRelease).where(
            WorkflowRelease.workflow_id == workflow.id,
            WorkflowRelease.project_id == workflow.project_id,
            WorkflowRelease.version == workflow.published_version,
        )
    )
    if release is None:
        return None
    try:
        nodes = release.definition.get("nodes")
        if (
            definition_digest(release.definition) != release.definition_digest
            or not isinstance(nodes, list)
            or sum(
                isinstance(node, dict) and node.get("type") == "webhook"
                for node in nodes
            )
            != 1
            or ValidationService.validate_workflow(
                release.definition,
                allow_webhook_preparation=True,
            )
        ):
            return None
    except (TypeError, ValueError, KeyError):
        return None
    return WebhookReleaseContext(
        release_id=release.id,
        definition_digest=release.definition_digest,
        version=release.version,
    )


async def list_deliveries(
    db: AsyncSession,
    workflow_id: str,
    project_id: UUID,
    limit: int,
    offset: int,
) -> WebhookDeliveryList:
    if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
        raise HTTPException(422, "投递记录分页范围无效。")
    workflow = await db.scalar(
        select(Workflow.id).where(
            Workflow.id == workflow_id,
            Workflow.project_id == str(project_id),
        )
    )
    if workflow is None:
        raise HTTPException(404, "工作流不存在。")
    owned = (
        WorkflowWebhookDelivery.project_id == str(project_id),
        WorkflowWebhookDelivery.workflow_id == workflow_id,
    )
    total = (
        await db.scalar(
            select(func.count())
            .select_from(
                WorkflowWebhookDelivery,
            )
            .where(*owned)
        )
        or 0
    )
    rows = await db.execute(
        select(
            WorkflowWebhookDelivery,
            WorkflowExecution.id,
            WorkflowExecution.status,
        )
        .outerjoin(
            WorkflowExecution,
            and_(
                WorkflowExecution.id == WorkflowWebhookDelivery.execution_id,
                WorkflowExecution.workflow_id == workflow_id,
                WorkflowExecution.project_id == str(project_id),
                WorkflowExecution.source_channel == "external_webhook",
                WorkflowExecution.execution_mode == "production",
                WorkflowExecution.release_id
                == WorkflowWebhookDelivery.release_id,
                WorkflowExecution.definition_digest
                == WorkflowWebhookDelivery.release_digest,
                WorkflowExecution.source_message_id
                == "webhook-" + WorkflowWebhookDelivery.id,
            ),
        )
        .where(*owned)
        .order_by(
            WorkflowWebhookDelivery.created_at.desc(),
            WorkflowWebhookDelivery.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    items = [
        WebhookDeliveryRecord(
            id=UUID(row.id),
            binding_revision=row.binding_revision,
            credential_revision=row.credential_revision,
            release_id=row.release_id,
            status=cast(DeliveryStatus, row.status),
            attempts=row.attempts,
            error_code=row.error_code,
            execution_id=run_id if run_id == execution_id(row.id) else None,
            execution_status=status
            if run_id == execution_id(row.id)
            else None,
            created_at=utc(row.created_at),
            expires_at=utc(row.expires_at),
        )
        for row, run_id, status in rows
    ]
    return WebhookDeliveryList(
        project_id=project_id,
        workflow_id=workflow_id,
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    )
