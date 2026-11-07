"""Desired screen scope; connection acknowledgement is deliberately not
durable."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DeviceScreenPolicy(Base):
    __tablename__ = "dc_device_screen_policies"
    __table_args__ = (
        CheckConstraint(
            "revision BETWEEN 1 AND 2147483647", name="ck_dc_screen_revision"
        ),
        CheckConstraint(
            "max_actions BETWEEN 1 AND 50", name="ck_dc_screen_actions"
        ),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dc_devices.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    connection_instance_id: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    grant_id: Mapped[str] = mapped_column(String(32), nullable=False)
    max_actions: Mapped[int] = mapped_column(Integer, nullable=False)
    allow_click: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allow_text: Mapped[bool] = mapped_column(Boolean, nullable=False)
    click_regions: Mapped[list[dict[str, int]]] = mapped_column(
        JSONB, nullable=False
    )
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
