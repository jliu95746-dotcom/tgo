"""Payload-free link from a customer AI attempt to a formal business query."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIExecutionBusinessQuery(Base):
    __tablename__ = "api_ai_execution_business_queries"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('order_query','logistics_query',"
            "'customer_profile_query','membership_query')",
            name="ck_ai_business_query_operation",
        ),
        CheckConstraint("outcome IN ('passed','failed')", name="ck_ai_business_query_outcome"),
        UniqueConstraint("execution_id", "query_id", name="uq_ai_execution_business_query"),
        Index("ix_ai_business_query_project_execution", "project_id", "execution_id"),
    )

    query_id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False,
    )
    execution_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_ai_execution_attempts.execution_id", ondelete="CASCADE"),
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
