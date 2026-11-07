"""Company-owned business connections; only the owner reads ciphertext."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BusinessConnection(Base):
    __tablename__ = "api_business_connections"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "request_id", name="uq_business_connection_request"
        ),
        CheckConstraint(
            (
                'revision >= 1 AND configuration_revision >= 1 AND '
                'authorization_revision >= 1'
            ),
            name="ck_business_connection_revisions",
        ),
        CheckConstraint(
            "mode IN ('http', 'demo')", name="ck_business_connection_mode"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_staff.id", ondelete="SET NULL")
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_staff.id", ondelete="SET NULL")
    )
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    configuration: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    credential_ciphertext: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    revision: Mapped[int] = mapped_column(default=1, nullable=False)
    configuration_revision: Mapped[int] = mapped_column(
        default=1, nullable=False
    )
    authorization_revision: Mapped[int] = mapped_column(
        default=1, nullable=False
    )
    verified_configuration_revision: Mapped[int | None] = mapped_column()
    verified_capabilities: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=list,
        nullable=False,
    )
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_tested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_staff.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
