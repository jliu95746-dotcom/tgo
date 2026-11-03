"""Keep the original administrator identity across an upstream wait."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.staff import Staff


@dataclass(frozen=True)
class ScheduleActor:
    project_id: UUID
    staff_id: UUID
    token_version: int

    @classmethod
    def from_staff(cls, staff: Staff) -> "ScheduleActor":
        return cls(staff.project_id, staff.id, staff.token_version)


def require_current_schedule_admin(db: Session, actor: ScheduleActor) -> None:
    current = db.scalar(
        schedule_admin_query(actor.project_id, actor.staff_id).where(
            Staff.token_version == actor.token_version
        )
    )
    if current is None:
        raise HTTPException(403, "当前管理员身份或权限已变化，请重新登录后读取原请求结果。")


def schedule_admin_query(
    project_id: UUID, staff_id: UUID
) -> Select[tuple[UUID]]:
    return (
        select(Staff.id)
        .join(Project)
        .where(
            Staff.id == staff_id,
            Staff.project_id == project_id,
            Staff.role == "admin",
            Staff.account_enabled.is_(True),
            Staff.deleted_at.is_(None),
            Project.deleted_at.is_(None),
        )
    )
