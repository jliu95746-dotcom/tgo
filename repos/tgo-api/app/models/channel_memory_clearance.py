from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ClearanceUserType(str, Enum):
    """User type for memory clearance."""

    STAFF = "staff"
    VISITOR = "visitor"


class ChannelMemoryClearance(Base):
    (
        'General channel memory clearance record table, supporting both '
        'Staff and Visitor user types.'
    )

    __tablename__ = "api_channel_memory_clearances"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("api_projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Generic user identifier: user_id + user_type combination
    user_id = Column(
        UUID(as_uuid=True), nullable=False
    )  # staff_id or visitor_id
    user_type = Column(String(20), nullable=False)  # "staff" or "visitor"

    channel_id = Column(String(255), nullable=False)
    channel_type = Column(Integer, nullable=False)
    cleared_message_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )  # message_seq at the time of clearance

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=True,
    )

    # Unique constraint: only one record per user + channel combination
    __table_args__ = (
        UniqueConstraint(
            'user_id',
            'user_type',
            'channel_id',
            'channel_type',
            name='uq_user_channel_clearance',
        ),
        Index(
            'ix_clearance_user_channel',
            'user_id',
            'user_type',
            'channel_id',
            'channel_type',
        ),
    )
