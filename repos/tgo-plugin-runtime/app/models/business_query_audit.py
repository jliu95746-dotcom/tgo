"""Persistent audit record for read-only business queries."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Float, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BusinessQueryAuditRecord(Base):
    """PII-minimized evidence for one order or logistics query."""

    __tablename__ = "pg_business_query_audit"
    __table_args__ = (
        Index(
            "ix_pg_business_query_audit_tenant_created",
            "tenant_id",
            "created_at",
        ),
        Index(
            "ix_pg_business_query_audit_tenant_request_created",
            "tenant_id", "request_id", "created_at", "id",
        ),
        Index(
            "ix_pg_business_query_audit_message_created",
            "tenant_id", "source_message_digest", "response_message_digest",
            "created_at", "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    visitor_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    parameter_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    source_message_digest: Mapped[str | None] = mapped_column(String(64))
    response_message_digest: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
