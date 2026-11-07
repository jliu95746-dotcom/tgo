"""Visitor tag model."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.tag import Tag
    from app.models.visitor import Visitor


class VisitorTag(Base):
    """Visitor tag model for many-to-many relationship between visitors and tags."""

    __tablename__ = "api_visitor_tags"

    # Primary key
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Foreign keys
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated project ID for multi-tenant isolation",
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id"),
        nullable=False,
        comment="Associated visitor ID",
    )
    tag_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("api_tags.id"),
        nullable=False,
        comment="Associated tag ID (Base64 encoded)",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now(), comment="Creation timestamp"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        comment="Last update timestamp",
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="Soft deletion timestamp"
    )

    # Relationships
    project: Mapped["Project"] = relationship(
        "Project", back_populates="visitor_tags", lazy="select"
    )

    visitor: Mapped["Visitor"] = relationship(
        "Visitor", back_populates="visitor_tags", lazy="select"
    )

    tag: Mapped["Tag"] = relationship(
        "Tag", back_populates="visitor_tags", lazy="select"
    )

    # Constraints
    __table_args__ = (
        UniqueConstraint(
            "visitor_id", "tag_id", name="uk_api_visitor_tags_visitor_tag"
        ),
    )

    def __repr__(self) -> str:
        """String representation of the visitor tag."""
        return (
            f"<VisitorTag(id={self.id}, "
            f"visitor_id={self.visitor_id}, "
            f"tag_id='{self.tag_id}')>"
        )

    @property
    def is_deleted(self) -> bool:
        """Check if the visitor tag is soft deleted."""
        return self.deleted_at is not None
