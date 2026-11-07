"""Visitor customer info update model."""

from datetime import datetime
from typing import Optional, TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, Text, func, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.visitor import Visitor


class VisitorCustomerUpdate(Base):
    (
        'Audit log for visitor customer info updates received from external '
        'services.'
    )

    __tablename__ = "api_visitor_customer_updates"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="Associated project ID for multi-tenant isolation",
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="CASCADE"),
        nullable=False,
        comment="Visitor whose information was updated",
    )

    source: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Source identifier for the update event",
    )
    channel_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Channel identifier associated with the update event",
    )
    channel_type: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Channel type associated with the update event",
    )
    customer_snapshot: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Snapshot of customer data provided in the event",
    )
    changes_applied: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment=(
            'Dictionary of applied changes {field: {"old": val, "new": val}}'
        ),
    )
    extra_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        comment="Additional metadata accompanying the update event",
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=func.now(),
        comment="Creation timestamp",
    )

    visitor: Mapped["Visitor"] = relationship(
        "Visitor",
        back_populates="customer_updates",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f'<VisitorCustomerUpdate(id='
            f'{self.id}'
            f', visitor_id='
            f'{self.visitor_id}'
            f')>'
        )
