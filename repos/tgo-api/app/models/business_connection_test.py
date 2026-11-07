"""Persistent, idempotent test attempts with bounded result metadata."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BusinessConnectionTest(Base):
    __tablename__ = "api_business_connection_tests"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "connection_id",
            "request_id",
            name="uq_connection_test_request",
        ),
        CheckConstraint(
            "status IN ('running','completed','failed','cancelled')",
            name="ck_connection_test_status",
        ),
        CheckConstraint(
            "mode IN ('http','demo')", name="ck_connection_test_mode"
        ),
        CheckConstraint(
            "operation IN ('order_query','logistics_query','customer_profile_query','membership_query')",
            name="ck_connection_test_operation",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_business_connections.id", ondelete="CASCADE"),
        index=True,
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_staff.id", ondelete="SET NULL")
    )
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_revision: Mapped[int] = mapped_column(nullable=False)
    configuration_revision: Mapped[int] = mapped_column(nullable=False)
    authorization_revision: Mapped[int] = mapped_column(nullable=False)
    resource_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="running", nullable=False
    )
    attempt_id: Mapped[UUID] = mapped_column(default=uuid4, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    receipt: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
