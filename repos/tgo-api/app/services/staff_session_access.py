"""Bind manual close/transfer commands to one staff token and one live session."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Platform, Project, Staff, Visitor, VisitorSession
from app.schemas.staff_data_access import StaffDataAccess
from app.services.profile_access import OperationGuard, not_found
from app.services.staff_record_access import (
    check_staff_record_permission,
    require_staff_record_access,
)
from app.services.visitor_chat_access import authority_database
from app.services.visitor_mutation_access import (
    changed,
    lock_permission,
    timestamp,
)


@dataclass(frozen=True)
class StaffSessionScope:
    access: StaffDataAccess
    session_id: UUID
    visitor_id: UUID
    platform_id: UUID | None
    visitor_platform_id: UUID
    original_staff_id: UUID | None
    original_version: datetime

    def check(
        self,
        db: Session,
        *,
        lock: bool = False,
        target_staff_id: UUID | None = None,
    ) -> VisitorSession:
        with db.no_autoflush:
            if lock:
                project = (
                    db.query(Project.id)
                    .filter(
                        Project.id == self.access.project_id,
                        Project.deleted_at.is_(None),
                    )
                    .with_for_update(read=True)
                    .first()
                )
                if project is None:
                    raise not_found()
            visitor_query = (
                db.query(Visitor)
                .filter(
                    Visitor.id == self.visitor_id,
                    Visitor.project_id == self.access.project_id,
                    Visitor.deleted_at.is_(None),
                    Visitor.access_retired_at.is_(None),
                )
                .populate_existing()
            )
            if lock:
                visitor_query = visitor_query.with_for_update()
            visitor = visitor_query.first()
            if (
                visitor is None
                or visitor.platform_id != self.visitor_platform_id
            ):
                raise not_found()
            platform_query = db.query(Platform.id).filter(
                Platform.id == self.visitor_platform_id,
                Platform.project_id == self.access.project_id,
                Platform.deleted_at.is_(None),
                Platform.is_active.is_(True),
            )
            if lock:
                platform_query = platform_query.with_for_update(read=True)
            if platform_query.first() is None:
                raise not_found()
            query = (
                db.query(VisitorSession)
                .filter(
                    VisitorSession.id == self.session_id,
                    VisitorSession.project_id == self.access.project_id,
                    VisitorSession.visitor_id == self.visitor_id,
                    VisitorSession.platform_id == self.platform_id,
                )
                .populate_existing()
            )
            if lock:
                query = query.with_for_update()
            session = query.first()
            if session is None:
                raise not_found()
            latest = (
                db.query(VisitorSession.id)
                .filter(
                    VisitorSession.project_id == self.access.project_id,
                    VisitorSession.visitor_id == self.visitor_id,
                    VisitorSession.status == "open",
                )
                .order_by(
                    VisitorSession.created_at.desc(), VisitorSession.id.desc()
                )
                .first()
            )
            if latest is None or latest[0] != self.session_id:
                raise changed()
            actor = (
                lock_permission(db, self.access, "visitors:update")
                if lock
                else check_staff_record_permission(
                    db,
                    self.access,
                    "visitors:update",
                )
            )
            if (
                session.status != "open"
                or session.staff_id != self.original_staff_id
                or timestamp(session.updated_at) != self.original_version
            ):
                raise changed()
            if session.staff_id != actor.id and actor.role != "admin":
                raise not_found()
            if target_staff_id is not None:
                if target_staff_id == self.original_staff_id:
                    raise changed()
                target_query = (
                    db.query(Staff)
                    .filter(
                        Staff.id == target_staff_id,
                        Staff.project_id == self.access.project_id,
                        Staff.deleted_at.is_(None),
                        Staff.account_enabled.is_(True),
                        Staff.is_active.is_(True),
                        Staff.service_paused.is_(False),
                        Staff.role.in_(["admin", "user"]),
                    )
                    .populate_existing()
                )
                if lock:
                    target_query = target_query.with_for_update(read=True)
                target = target_query.first()
                if target is None or (
                    settings.SAAS_BILLING_ENABLED
                    and not target.is_available_for_service
                ):
                    raise changed()
            return session

    def completed_guard(
        self, session: VisitorSession, *, status: str, staff_id: UUID | None
    ) -> OperationGuard:
        completed_version = timestamp(session.updated_at)

        def check() -> None:
            with authority_database() as db:
                check_staff_record_permission(
                    db, self.access, "visitors:update"
                )
                current = (
                    db.query(VisitorSession)
                    .filter(
                        VisitorSession.id == self.session_id,
                        VisitorSession.project_id == self.access.project_id,
                        VisitorSession.visitor_id == self.visitor_id,
                        VisitorSession.platform_id == self.platform_id,
                    )
                    .populate_existing()
                    .first()
                )
                if (
                    current is None
                    or current.status != status
                    or current.staff_id != staff_id
                    or timestamp(current.updated_at) != completed_version
                ):
                    raise changed()
                visitor = (
                    db.query(Visitor.id)
                    .filter(
                        Visitor.id == self.visitor_id,
                        Visitor.project_id == self.access.project_id,
                        Visitor.platform_id == self.visitor_platform_id,
                        Visitor.deleted_at.is_(None),
                        Visitor.access_retired_at.is_(None),
                    )
                    .first()
                )
                if visitor is None:
                    raise not_found()
                platform = (
                    db.query(Platform.id)
                    .filter(
                        Platform.id == self.visitor_platform_id,
                        Platform.project_id == self.access.project_id,
                        Platform.deleted_at.is_(None),
                        Platform.is_active.is_(True),
                    )
                    .first()
                )
                if platform is None:
                    raise not_found()
                if status == "open" and staff_id is not None:
                    target = (
                        db.query(Staff)
                        .filter(
                            Staff.id == staff_id,
                            Staff.project_id == self.access.project_id,
                            Staff.deleted_at.is_(None),
                            Staff.account_enabled.is_(True),
                            Staff.is_active.is_(True),
                            Staff.service_paused.is_(False),
                            Staff.role.in_(["admin", "user"]),
                        )
                        .first()
                    )
                    if target is None:
                        raise changed()

        return OperationGuard(check)


def begin_staff_session_change(
    db: Session,
    credentials: HTTPAuthorizationCredentials | None,
    actor: Staff,
    *,
    session_id: UUID | None = None,
    visitor_id: UUID | None = None,
) -> tuple[VisitorSession, StaffSessionScope]:
    access = require_staff_record_access(
        db, credentials, actor, "visitors:update"
    )
    query = db.query(VisitorSession).filter(
        VisitorSession.project_id == access.project_id,
        VisitorSession.status == "open",
    )
    if session_id is not None:
        query = query.filter(VisitorSession.id == session_id)
    if visitor_id is not None:
        query = query.filter(VisitorSession.visitor_id == visitor_id)
    session = query.order_by(
        VisitorSession.created_at.desc(), VisitorSession.id.desc()
    ).first()
    if session is None:
        raise not_found()
    visitor = (
        db.query(Visitor)
        .filter(
            Visitor.id == session.visitor_id,
            Visitor.project_id == access.project_id,
            Visitor.deleted_at.is_(None),
        )
        .first()
    )
    if visitor is None:
        raise not_found()
    scope = StaffSessionScope(
        access,
        session.id,
        session.visitor_id,
        session.platform_id,
        visitor.platform_id,
        session.staff_id,
        timestamp(session.updated_at),
    )
    scope.check(db)
    return session, scope
