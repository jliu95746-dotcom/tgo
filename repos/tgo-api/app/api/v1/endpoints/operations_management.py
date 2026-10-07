"""Independent operator management; merchant tokens never grant access."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.v1.endpoints.operations import (
    require_commercial_operations,
    require_operator,
)
from app.core.database import get_db
from app.models.platform_operator import PlatformOperator
from app.schemas.operations_management import (
    AccountEmailAction,
    AuthorizationChange,
    AuthorizationPreview,
    CompanyDetail,
    CompanyDirectory,
    ManagedMember,
    MemberControl,
    OperationsOverview,
    OperatorAuditView,
)
from app.services import operations_management as service
from app.services.operations_authorization import (
    change_authorization,
    preview_authorization,
)

router = APIRouter(
    dependencies=[Depends(require_commercial_operations), Depends(require_operator)]
)


@router.get("/overview", response_model=OperationsOverview)
def overview(db: Session = Depends(get_db)) -> OperationsOverview:
    return service.overview(db)


@router.get("/company-directory", response_model=CompanyDirectory)
def directory(
    q: str = Query("", max_length=254),
    state: Literal["legacy", "pending", "trial", "active", "expired", "suspended", "enabled"]
    | None = None,
    expiring: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> CompanyDirectory:
    return service.directory(db, offset, limit, q=q, state=state, expiring=expiring)


@router.get("/companies/{project_id}", response_model=CompanyDetail)
def detail(project_id: UUID, db: Session = Depends(get_db)) -> CompanyDetail:
    return service.company_detail(db, project_id)


@router.post(
    "/companies/{project_id}/authorization/preview", response_model=AuthorizationPreview
)
def preview(
    project_id: UUID, payload: AuthorizationChange, db: Session = Depends(get_db)
) -> AuthorizationPreview:
    return preview_authorization(db, project_id, payload)


@router.put("/companies/{project_id}/authorization", response_model=CompanyDetail)
def authorization(
    project_id: UUID,
    payload: AuthorizationChange,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> CompanyDetail:
    change_authorization(db, operator, project_id, payload)
    db.commit()
    return service.company_detail(db, project_id)


@router.patch(
    "/companies/{project_id}/members/{member_id}", response_model=ManagedMember
)
def member(
    project_id: UUID,
    member_id: UUID,
    payload: MemberControl,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> ManagedMember:
    result = service.update_member(db, operator, project_id, member_id, payload)
    db.commit()
    return result


@router.post("/companies/{project_id}/members/{member_id}/reset-email", status_code=204)
def reset_email(
    project_id: UUID,
    member_id: UUID,
    payload: AccountEmailAction,
    db: Session = Depends(get_db),
    operator: PlatformOperator = Depends(require_operator),
) -> Response:
    service.send_reset_email(db, operator, project_id, member_id, payload.reason)
    db.commit()
    return Response(status_code=204)


@router.get("/audit-log", response_model=list[OperatorAuditView])
def audit_log(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    project_id: UUID | None = None,
    db: Session = Depends(get_db),
) -> list[OperatorAuditView]:
    return service.audit_log(db, offset, limit, project_id)
