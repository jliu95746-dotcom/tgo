"""Visitor AI profile model."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.visitor import Visitor


class VisitorAIProfile(Base):
    """Aggregated AI persona data for a visitor."""

    __tablename__ = "api_visitor_ai_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated project ID for multi-tenant isolation"
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated visitor ID"
    )
    persona_tags: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment="List of AI generated persona tags"
    )
    summary: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Structured summary data for AI persona"
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
        comment="Creation timestamp"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        comment="Last update timestamp"
    )

    visitor: Mapped["Visitor"] = relationship(
        "Visitor",
        back_populates="ai_profile",
        lazy="select"
    )

    __table_args__ = (
        UniqueConstraint("visitor_id", name="uk_api_visitor_ai_profile_visitor"),
    )

    def __repr__(self) -> str:
        return f"<VisitorAIProfile(visitor_id={self.visitor_id})>"
