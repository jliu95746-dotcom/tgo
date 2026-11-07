"""Visitor system information model."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.visitor import Visitor


class VisitorSystemInfo(Base):
    """System metadata captured for a visitor's sessions."""

    __tablename__ = "api_visitor_system_info"

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
    platform: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Acquisition or support platform name",
    )
    source_detail: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Additional context for the visitor source",
    )
    browser: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="Latest known browser"
    )
    operating_system: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="Latest known operating system"
    )
    first_seen_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="Timestamp of the first tracked session"
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="Timestamp of the last tracked session"
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
        "Visitor", back_populates="system_info", lazy="select"
    )

    __table_args__ = (
        UniqueConstraint(
            "visitor_id", name="uk_api_visitor_system_info_visitor"
        ),
    )

    def __repr__(self) -> str:
        return f"<VisitorSystemInfo(visitor_id={self.visitor_id})>"
