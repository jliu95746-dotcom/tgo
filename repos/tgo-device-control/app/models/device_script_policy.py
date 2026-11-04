"""Durable script scope; live acknowledgements stay on the connection."""

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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DeviceScriptPolicy(Base):
    __tablename__ = "dc_device_script_policies"
    __table_args__ = (
        CheckConstraint(
            "revision BETWEEN 1 AND 2147483647", name="ck_dc_script_revision"
        ),
        CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 120", name="ck_dc_script_timeout"
        ),
        CheckConstraint(
            "max_output_bytes BETWEEN 256 AND 65536",
            name="ck_dc_script_output",
        ),
        CheckConstraint(
            "max_runs BETWEEN 1 AND 100", name="ck_dc_script_runs"
        ),
        CheckConstraint(
            "enabled = (revoked_at IS NULL)", name="ck_dc_script_revocation"
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
    script_id: Mapped[str] = mapped_column(String(48), nullable=False)
    grant_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_output_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    max_runs: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
