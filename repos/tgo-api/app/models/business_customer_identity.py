"""Merchant issuer keys and customer bindings belong to the API owner."""

from datetime import datetime
from uuid import UUID, uuid4

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


class BusinessCustomerIdentityPolicy(Base):
    __tablename__ = "api_business_customer_identity_policies"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "connection_id",
            name="uq_customer_identity_policy_connection",
        ),
        CheckConstraint(
            (
                'revision >= 1 AND connection_configuration_revision >= 1 '
                'AND max_session_seconds BETWEEN 60 AND 3600'
            ),
            name="ck_customer_identity_policy_limits",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_business_connections.id", ondelete="CASCADE"),
        index=True,
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_staff.id", ondelete="SET NULL")
    )
    platform_ids: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    signing_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    revision: Mapped[int] = mapped_column(default=1, nullable=False)
    connection_configuration_revision: Mapped[int] = mapped_column(
        nullable=False
    )
    max_session_seconds: Mapped[int] = mapped_column(
        default=900, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BusinessCustomerBinding(Base):
    __tablename__ = "api_business_customer_bindings"
    __table_args__ = (
        UniqueConstraint(
            "policy_id", "nonce", name="uq_customer_binding_nonce"
        ),
        CheckConstraint(
            (
                'revision >= 1 AND policy_revision >= 1 AND '
                'connection_configuration_revision >= 1'
            ),
            name="ck_customer_binding_revisions",
        ),
        CheckConstraint(
            "expires_at > issued_at", name="ck_customer_binding_expiry"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_business_connections.id", ondelete="CASCADE"),
        index=True,
    )
    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "api_business_customer_identity_policies.id", ondelete="CASCADE"
        )
    )
    visitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_visitors.id", ondelete="CASCADE"), index=True
    )
    platform_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_platforms.id", ondelete="CASCADE")
    )
    nonce: Mapped[UUID] = mapped_column(nullable=False)
    proof_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    policy_revision: Mapped[int] = mapped_column(nullable=False)
    connection_configuration_revision: Mapped[int] = mapped_column(
        nullable=False
    )
    revision: Mapped[int] = mapped_column(default=1, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
