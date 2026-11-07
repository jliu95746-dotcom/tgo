"""Visitor activity model."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.visitor import Visitor


class VisitorActivity(Base):
    """Timeline entry representing a visitor activity."""

    __tablename__ = "api_visitor_activities"

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
    activity_type: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="Categorised type of activity"
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Short title or headline for the activity",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Detailed description of the activity"
    )
    context: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Arbitrary structured context for the activity",
    )
    duration_seconds: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Duration associated with the activity, in seconds",
    )
    occurred_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
        comment="When the activity occurred",
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now(), comment="Record creation timestamp"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        comment="Record update timestamp",
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="Soft deletion timestamp"
    )

    visitor: Mapped["Visitor"] = relationship(
        "Visitor", back_populates="activities", lazy="select"
    )

    def __repr__(self) -> str:
        return (
            f'<VisitorActivity(id='
            f'{self.id}'
            f', visitor_id='
            f'{self.visitor_id}'
            f", type='"
            f'{self.activity_type}'
            f"')>"
        )
