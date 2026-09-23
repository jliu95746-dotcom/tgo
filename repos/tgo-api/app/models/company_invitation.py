"""Pending invitations reserve seats without enabling unverified accounts."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CompanyInvitation(Base):
    __tablename__ = "api_company_invitations"
    __table_args__ = (
        CheckConstraint("role IN ('admin','user')", name="ck_invitation_role"),
        CheckConstraint(
            "status IN ('pending','accepted','revoked')",
            name="ck_invitation_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("api_projects.id"), index=True)
    staff_id: Mapped[UUID] = mapped_column(ForeignKey("api_staff.id"))
    action_id: Mapped[UUID] = mapped_column(
        ForeignKey("api_email_actions.id"), unique=True
    )
    email: Mapped[str] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(20), default="user")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
