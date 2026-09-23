"""Platform operators are independent of company staff and their roles."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Integer, String, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PlatformOperator(Base):
    __tablename__ = "api_platform_operators"
    __table_args__ = (
        CheckConstraint(
            "token_version >= 1", name="ck_operator_token_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(
        String(254), nullable=False, unique=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    token_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
