"""Tenant-scoped, bounded audit reads without query parameters or PII."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_query_audit import BusinessQueryAuditRecord
from app.domain.business_tools.models import message_lookup_digest
from app.schemas.business_query_audit import (
    BusinessQueryAuditItem,
    BusinessQueryAuditList,
    BusinessMessageAuditQuery,
)


async def list_query_audits(
    db: AsyncSession,
    project_id: UUID,
    query_id: UUID,
) -> BusinessQueryAuditList:
    scope = (
        BusinessQueryAuditRecord.tenant_id == project_id,
        BusinessQueryAuditRecord.request_id == str(query_id),
    )
    total = await db.scalar(
        select(func.count())
        .select_from(BusinessQueryAuditRecord)
        .where(*scope)
    )
    rows = (
        (
            await db.execute(
                select(BusinessQueryAuditRecord)
                .where(*scope)
                .order_by(
                    BusinessQueryAuditRecord.created_at.desc(),
                    BusinessQueryAuditRecord.id.desc(),
                )
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    if any(
        row.tenant_id != project_id or row.request_id != str(query_id)
        for row in rows
    ):
        raise ValueError("Business query audit scope mismatch")
    return BusinessQueryAuditList(
        total=total or 0,
        events=[
            BusinessQueryAuditItem.model_validate(
                {
                    "id": row.id,
                    "project_id": row.tenant_id,
                    "query_id": query_id,
                    "agent_id": row.actor_id,
                    "operation": row.operation,
                    "outcome": row.outcome,
                    "duration_ms": row.duration_ms,
                    "created_at": row.created_at,
                }
            )
            for row in rows
        ],
    )


async def list_message_query_audits(
    db: AsyncSession,
    query: BusinessMessageAuditQuery,
) -> BusinessQueryAuditList:
    """Find formal audit observations by the two signed message identities."""
    source_digest = message_lookup_digest(
        query.project_id, query.source_message_id
    )
    response_digest = message_lookup_digest(
        query.project_id, query.response_client_msg_no
    )
    scope = (
        BusinessQueryAuditRecord.tenant_id == query.project_id,
        BusinessQueryAuditRecord.source_message_digest == source_digest,
        BusinessQueryAuditRecord.response_message_digest == response_digest,
    )
    total = await db.scalar(
        select(func.count())
        .select_from(BusinessQueryAuditRecord)
        .where(*scope)
    )
    rows = (
        (
            await db.execute(
                select(BusinessQueryAuditRecord)
                .where(*scope)
                .order_by(
                    BusinessQueryAuditRecord.created_at,
                    BusinessQueryAuditRecord.id,
                )
                .limit(query.limit)
                .offset(query.skip)
            )
        )
        .scalars()
        .all()
    )
    if any(
        row.tenant_id != query.project_id
        or row.source_message_digest != source_digest
        or row.response_message_digest != response_digest
        for row in rows
    ):
        raise ValueError("Business message audit scope mismatch")
    return BusinessQueryAuditList(
        total=total or 0,
        events=[
            BusinessQueryAuditItem.model_validate(
                {
                    "id": row.id,
                    "project_id": row.tenant_id,
                    "query_id": UUID(row.request_id),
                    "agent_id": UUID(row.actor_id),
                    "operation": row.operation,
                    "outcome": row.outcome,
                    "duration_ms": row.duration_ms,
                    "created_at": row.created_at,
                }
            )
            for row in rows
        ],
    )
