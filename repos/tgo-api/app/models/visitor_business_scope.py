"""An irreversible business customer owner for one visitor and connection."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VisitorBusinessScope(Base):
    __tablename__ = "api_visitor_business_scopes"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "visitor_id",
            "connection_id",
            name="uq_visitor_business_scope_owner",
        ),
        CheckConstraint("revision >= 1", name="ck_visitor_business_scope_revision"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), index=True
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_business_connections.id", ondelete="CASCADE"), index=True
    )
    owner_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(default=1, server_default="1", nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
