"""Read-only, project-scoped staff context for internal consumers."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Project, Staff
from app.schemas.internal_staff import InternalStaffResponse


def get_internal_staff_info(
    db: Session, staff_id: UUID, project_id: UUID,
) -> InternalStaffResponse | None:
    staff = (
        db.query(Staff)
        .join(Project, Project.id == Staff.project_id)
        .filter(
            Staff.id == staff_id,
            Staff.project_id == project_id,
            Staff.deleted_at.is_(None),
            Project.deleted_at.is_(None),
        )
        .first()
    )
    if staff is None:
        return None
    return InternalStaffResponse.model_validate(staff)
