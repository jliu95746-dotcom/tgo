"""Bind plugin administration and catalog reads to current staff authority."""

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user, require_admin, security
from app.models import Staff
from app.schemas.staff_data_access import StaffDataAccess
from app.services.profile_access import profile_guard, require_profile_access
from app.services.visitor_access import denied


def _bind_authority(
    request: Request,
    staff: Staff,
    db: Session,
    credentials: HTTPAuthorizationCredentials,
) -> Staff:
    access = require_profile_access(db, credentials, None)
    if (
        not isinstance(access, StaffDataAccess)
        or access.staff_id != staff.id
        or access.project_id != staff.project_id
        or access.role != staff.role
    ):
        raise denied()
    guard = profile_guard(access)
    guard.check()
    request.state.operation_guard = guard
    return staff


async def plugin_administrator(
    request: Request,
    staff: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Staff:
    return _bind_authority(request, staff, db, credentials)


async def plugin_catalog_reader(
    request: Request,
    staff: Staff = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Staff:
    return _bind_authority(request, staff, db, credentials)
