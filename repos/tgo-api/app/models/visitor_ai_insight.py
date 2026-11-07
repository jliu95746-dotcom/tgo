"""Visitor AI insight model."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.visitor import Visitor


class VisitorAIInsight(Base):
    """AI derived insight metrics for a visitor."""

    __tablename__ = "api_visitor_ai_insights"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated project ID for multi-tenant isolation",
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated visitor ID",
    )
    satisfaction_score: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Latest satisfaction score on a 0-5 scale (0=unknown)",
    )
    emotion_score: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Latest emotion score on a 0-5 scale (0=unknown)",
    )
    intent: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment=(
            'Visitor intent classification (e.g., purchase, inquiry, '
            'complaint, support)'
        ),
    )
    insight_summary: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Brief natural language summary of the insight",
    )
    insight_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        comment="Additional metadata for the AI insight",
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now(), comment="Creation timestamp"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        comment="Last update timestamp",
    )

    visitor: Mapped["Visitor"] = relationship(
        "Visitor", back_populates="ai_insight", lazy="select"
    )

    __table_args__ = (
        UniqueConstraint(
            "visitor_id", name="uk_api_visitor_ai_insight_visitor"
        ),
    )

    def __repr__(self) -> str:
        return f"<VisitorAIInsight(visitor_id={self.visitor_id})>"
