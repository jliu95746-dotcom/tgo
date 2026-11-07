"""Channel member model for WuKongIM customer service channels."""

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    String,
    UniqueConstraint,
    Index,
    Integer,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ChannelMember(Base):
    """Tracks members (visitors and staff) in a WuKongIM channel."""

    __tablename__ = "api_channel_members"

    # Primary key
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Foreign keys and references
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated project ID for multi-tenant isolation",
    )

    # WuKongIM channel identifiers
    channel_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="WuKongIM channel ID (Base62-encoded)",
    )
    channel_type: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="WuKongIM channel type (e.g., 251 for customer service)",
    )

    # Member (visitor or staff)
    member_id: Mapped[UUID] = mapped_column(
        nullable=False,
        comment="Member UUID (either Visitor.id or Staff.id)",
    )
    member_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Member type: 'visitor' or 'staff'",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
        comment="Creation timestamp",
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        comment="Last update timestamp",
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        comment="Soft deletion timestamp",
    )

    # Constraints and Indexes
    __table_args__ = (
        # Prevent duplicates while allowing re-add after soft delete
        UniqueConstraint(
            "channel_id",
            "member_id",
            "deleted_at",
            name="uk_api_channel_members_channel_member_deleted",
        ),
        CheckConstraint(
            "member_type IN ('visitor', 'staff')",
            name="chk_api_channel_members_type",
        ),
        Index("ix_api_channel_members_project_id", "project_id"),
        Index("ix_api_channel_members_member_id", "member_id"),
        Index(
            "ix_api_channel_members_channel_member", "channel_id", "member_id"
        ),
    )
