"""Operator-only recovery primitives; deliberately not exposed by a public API."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models import Project, Staff


@dataclass(frozen=True)
class RecoveryTarget:
    staff_id: UUID
    username: str
    project_id: UUID
    project_name: str


def find_recovery_target(db: Session, username: str) -> RecoveryTarget:
    username = username.strip()
    base = (
        select(Staff, Project)
        .join(Project, Staff.project_id == Project.id)
        .where(
            Staff.deleted_at.is_(None),
            Project.deleted_at.is_(None),
            Staff.role.in_(["admin", "user"]),
        )
    )
    row = db.execute(base.where(Staff.username == username)).first()
    if row is None and "@" in username:
        matches = db.execute(
            base.where(func.lower(Staff.username) == username.lower())
        ).all()
        if len(matches) == 1:
            row = matches[0]
    if row is None:
        raise ValueError("未找到唯一的有效账号，请核对完整邮箱或用户名。")
    staff, project = row
    return RecoveryTarget(staff.id, staff.username, project.id, project.name)


def reset_confirmed_password(
    db: Session, staff_id: UUID, project_id: UUID, password: str
) -> None:
    if len(password) < 8 or len(password.encode("utf-8")) > 72:
        raise ValueError("密码至少 8 位，且 UTF-8 编码不能超过 72 字节。")
    try:
        staff = db.scalars(
            select(Staff)
            .join(Project, Staff.project_id == Project.id)
            .where(
                Staff.id == staff_id,
                Staff.project_id == project_id,
                Staff.deleted_at.is_(None),
                Project.deleted_at.is_(None),
                Staff.role.in_(["admin", "user"]),
            )
            .with_for_update()
        ).first()
        if staff is None:
            raise ValueError("账号或项目已失效，未修改密码。")
        staff.password_hash = get_password_hash(password)
        db.commit()
    except Exception:
        db.rollback()
        raise
