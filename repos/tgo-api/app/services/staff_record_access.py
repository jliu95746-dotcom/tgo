"""Original staff authority and current permission for private record reads."""

from uuid import UUID

from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.core.security import check_user_permission
from app.models import Staff, Visitor
from app.schemas.staff_data_access import StaffDataAccess
from app.services.profile_access import (
    OperationGuard,
    check_profile_access,
    require_profile_access,
    not_found,
)
from app.services.staff_conversation_scope import (
    owned_visitor_ids,
    restricted_staff,
)
from app.services.visitor_access import denied
from app.services.visitor_chat_access import authority_database


def check_staff_record_permission(
    db: Session,
    access: StaffDataAccess,
    permission: str,
) -> Staff:
    staff = check_profile_access(db, access)
    assert isinstance(staff, Staff)
    if not check_user_permission(db, staff, permission):
        raise TGOAPIException("没有执行该客户操作的权限", "FORBIDDEN", status_code=403)
    return staff


def require_staff_record_access(
    db: Session,
    credentials: HTTPAuthorizationCredentials | None,
    expected_staff: Staff,
    permission: str,
) -> StaffDataAccess:
    access = require_profile_access(db, credentials, None)
    if (
        not isinstance(access, StaffDataAccess)
        or access.staff_id != expected_staff.id
    ):
        raise denied()
    check_staff_record_permission(db, access, permission)
    return access


def staff_record_guard(
    access: StaffDataAccess,
    visitor_ids: tuple[UUID, ...],
    permission: str,
) -> OperationGuard:
    def check() -> None:
        with authority_database() as db:
            staff = check_staff_record_permission(db, access, permission)
            if not visitor_ids:
                return
            query = select(Visitor.id).where(
                Visitor.id.in_(visitor_ids),
                Visitor.project_id == access.project_id,
                Visitor.deleted_at.is_(None),
            )
            if restricted_staff(staff):
                query = query.where(Visitor.id.in_(owned_visitor_ids(staff)))
            if set(db.scalars(query).all()) != set(visitor_ids):
                raise not_found()

    return OperationGuard(check)
