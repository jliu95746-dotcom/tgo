"""Current, serialized staff writes bound to their original customer version."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import TypeAlias
from uuid import UUID

from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models import (
    Permission, Platform, Project, ProjectRolePermission, RolePermission,
    Staff, Visitor, VisitorSession,
)
from app.schemas.staff_data_access import StaffDataAccess
from app.services.profile_access import OperationGuard, not_found
from app.services.staff_conversation_scope import require_owned_visitor
from app.services.staff_record_access import (
    check_staff_record_permission, require_staff_record_access,
)
from app.services.visitor_chat_access import authority_database

VisitorChange: TypeAlias = Callable[[Visitor], None]


def timestamp(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def version(visitor: Visitor) -> datetime:
    value: datetime = visitor.updated_at
    return timestamp(value)


def changed() -> TGOAPIException:
    return TGOAPIException("客户状态已变化，请刷新后重试", "VISITOR_STATE_CHANGED", status_code=409)


def lock_project(db: Session, access: StaffDataAccess) -> None:
    if db.query(Project.id).filter(Project.id == access.project_id, Project.deleted_at.is_(None)).with_for_update(read=True).first() is None:
        raise not_found()


def lock_permission(db: Session, access: StaffDataAccess, permission: str) -> Staff:
    # Recheck the original token after obtaining the member lock. Permission
    # grants used by a non-admin are also locked until this transaction commits.
    db.query(Staff).filter(Staff.id == access.staff_id).populate_existing().with_for_update(read=True).first()
    staff = check_staff_record_permission(db, access, permission)
    if staff.role != "admin":
        resource, action = permission.split(":", 1)
        inherited = db.query(RolePermission).join(Permission).filter(
            RolePermission.role == staff.role, Permission.resource == resource,
            Permission.action == action,
        ).with_for_update(read=True, of=(RolePermission, Permission)).all()
        project_grants = db.query(ProjectRolePermission).join(Permission).filter(
            ProjectRolePermission.role == staff.role,
            ProjectRolePermission.project_id == access.project_id,
            Permission.resource == resource, Permission.action == action,
        ).with_for_update(read=True, of=(ProjectRolePermission, Permission)).all()
        if not inherited and not project_grants:
            raise TGOAPIException("没有执行该客户操作的权限", "FORBIDDEN", status_code=403)
    return staff


@dataclass(frozen=True)
class VisitorMutationScope:
    access: StaffDataAccess
    permission: str
    visitor_id: UUID
    platform_id: UUID
    original_version: datetime
    owner_required: bool = True
    allow_deleted: bool = False
    claim: bool = False

    def check(self, db: Session, *, lock: bool = False) -> Visitor:
        with db.no_autoflush:
            if lock:
                lock_project(db, self.access)
            query = db.query(Visitor).filter(
                Visitor.id == self.visitor_id, Visitor.project_id == self.access.project_id,
                Visitor.platform_id == self.platform_id,
            ).populate_existing()
            if lock:
                query = query.with_for_update()
            visitor: Visitor | None = query.first()
            if visitor is None or (visitor.deleted_at is not None) != self.allow_deleted:
                raise not_found()
            platform_query = db.query(Platform).filter(
                Platform.id == self.platform_id, Platform.project_id == self.access.project_id,
                Platform.deleted_at.is_(None),
            )
            if lock:
                platform_query = platform_query.with_for_update(read=True)
            if platform_query.first() is None:
                raise not_found()
            staff = lock_permission(db, self.access, self.permission) if lock else check_staff_record_permission(db, self.access, self.permission)
            if self.owner_required:
                if lock:
                    # Ownership may be changed on the session without updating
                    # the customer row. Hold that assignment through commit too.
                    db.query(VisitorSession).filter(
                        VisitorSession.visitor_id == self.visitor_id,
                        VisitorSession.project_id == self.access.project_id,
                    ).order_by(VisitorSession.created_at.desc(), VisitorSession.id.desc()).populate_existing().with_for_update(read=True).first()
                require_owned_visitor(db, staff, self.visitor_id)
            if self.claim:
                if visitor.access_retired_at is not None:
                    raise not_found()
                latest = db.query(VisitorSession).filter(
                    VisitorSession.visitor_id == self.visitor_id,
                    VisitorSession.project_id == self.access.project_id,
                ).order_by(VisitorSession.created_at.desc(), VisitorSession.id.desc()).first()
                if visitor.service_status == "active" or (latest is not None and latest.status == "open" and latest.staff_id is not None):
                    raise changed()
            if version(visitor) != self.original_version:
                raise changed()
            return visitor

    def guard(self) -> OperationGuard:
        def check() -> None:
            with authority_database() as db:
                self.check(db)
        return OperationGuard(check)

    def after_commit(self, visitor: Visitor) -> "VisitorMutationScope":
        return replace(self, original_version=version(visitor), allow_deleted=visitor.deleted_at is not None,
            owner_required=(self.owner_required or self.claim) and visitor.deleted_at is None, claim=False)


def begin_mutation(
    db: Session, credentials: HTTPAuthorizationCredentials | None, staff: Staff,
    visitor_id: UUID, permission: str, *, claim: bool = False,
) -> VisitorMutationScope:
    access = require_staff_record_access(db, credentials, staff, permission)
    visitor = db.query(Visitor).filter(
        Visitor.id == visitor_id, Visitor.project_id == access.project_id,
        Visitor.deleted_at.is_(None),
    ).populate_existing().first()
    if visitor is None:
        raise not_found()
    scope = VisitorMutationScope(access, permission, visitor.id, visitor.platform_id, version(visitor), owner_required=not claim, claim=claim)
    scope.check(db)
    return scope


def commit_change(db: Session, scope: VisitorMutationScope, apply: VisitorChange) -> tuple[Visitor, VisitorMutationScope]:
    try:
        visitor = scope.check(db, lock=True)
        apply(visitor)
        if (visitor.id, visitor.project_id, visitor.platform_id) != (scope.visitor_id, scope.access.project_id, scope.platform_id):
            raise not_found()
        visitor.updated_at = max(datetime.utcnow(), scope.original_version + timedelta(microseconds=1))
        db.commit()
        db.refresh(visitor)
        return visitor, scope.after_commit(visitor)
    except BaseException:
        db.rollback()
        raise


def create_customer(db: Session, access: StaffDataAccess, visitor: Visitor) -> tuple[Visitor, VisitorMutationScope]:
    try:
        with db.no_autoflush:
            lock_project(db, access)
            platform = db.query(Platform).filter(
                Platform.id == visitor.platform_id, Platform.project_id == access.project_id,
                Platform.deleted_at.is_(None), Platform.is_active.is_(True),
            ).with_for_update().first()
            if platform is None or visitor.project_id != access.project_id:
                raise not_found()
            lock_permission(db, access, "visitors:create")
            if db.query(Visitor.id).filter(
                Visitor.project_id == access.project_id, Visitor.platform_id == visitor.platform_id,
                Visitor.platform_open_id == visitor.platform_open_id,
            ).first() is not None:
                raise changed()
        db.add(visitor)
        db.commit()
        db.refresh(visitor)
        scope = VisitorMutationScope(access, "visitors:create", visitor.id, visitor.platform_id, version(visitor), owner_required=False)
        return visitor, scope
    except BaseException:
        db.rollback()
        raise
