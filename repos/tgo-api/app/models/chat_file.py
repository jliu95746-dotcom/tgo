"""Chat file metadata model."""

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ChatFile(Base):
    """Stores metadata for uploaded chat files."""

    __tablename__ = "api_chat_files"

    # Primary key
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Multi-tenant isolation
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated project ID",
    )

    # Channel context
    channel_id: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Channel identifier"
    )
    channel_type: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Channel type code"
    )

    # File metadata
    file_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Original filename"
    )
    file_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        comment="Relative path from base upload directory",
    )
    file_size: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="File size in bytes"
    )
    file_type: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="MIME type"
    )

    # Who uploaded
    uploaded_by_staff_id: Mapped[Optional[UUID]] = mapped_column(
        nullable=True, comment="Staff ID if uploaded by staff"
    )
    uploaded_by_platform_id: Mapped[Optional[UUID]] = mapped_column(
        nullable=True, comment="Platform ID if uploaded via platform API key"
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now(), comment="Creation time"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        comment="Update time",
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="Soft deletion time"
    )
