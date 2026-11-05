"""Reserve one current staff seat until the owning transaction finishes."""

import hashlib
from uuid import UUID

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models import Staff, VisitorSession


def try_lock_staff_seat(db: Session, project_id: UUID, staff_id: UUID) -> bool:
    """Do not wait behind another assignment while holding a visitor lock."""
    if db.get_bind().dialect.name != "postgresql":
        return True
    digest = hashlib.sha256(
        b"staff-assignment-seat:" + project_id.bytes + staff_id.bytes
    ).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    return bool(
        db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}
        ).scalar_one()
    )


def reserve_staff_seat(
    db: Session,
    project_id: UUID,
    staff_id: UUID,
    session_id: UUID | None,
    capacity: int | None,
) -> bool:
    if capacity is not None and not try_lock_staff_seat(
        db, project_id, staff_id
    ):
        return False
    staff: Staff | None = (
        db.query(Staff)
        .filter(
            Staff.id == staff_id,
            Staff.project_id == project_id,
            Staff.deleted_at.is_(None),
            Staff.account_enabled.is_(True),
            Staff.is_active.is_(True),
            Staff.service_paused.is_(False),
            Staff.role.in_(["admin", "user"]),
        )
        .populate_existing()
        .with_for_update(read=True, skip_locked=True)
        .first()
    )
    if staff is None:
        return False
    if capacity is None:
        return True
    query = db.query(func.count(VisitorSession.id)).filter(
        VisitorSession.project_id == project_id,
        VisitorSession.staff_id == staff_id,
        VisitorSession.status == "open",
    )
    if session_id is not None:
        query = query.filter(VisitorSession.id != session_id)
    occupied: int = query.scalar() or 0
    return occupied < capacity
