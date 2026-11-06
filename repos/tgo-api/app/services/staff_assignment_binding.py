"""Bind an in-flight decision to reception, not mutable message counters."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Platform, Project, Visitor, VisitorSession
from app.services.visitor_mutation_access import changed


@dataclass(frozen=True)
class ReceptionSession:
    id: UUID
    status: str
    staff_id: UUID | None


@dataclass(frozen=True)
class ReceptionBinding:
    visitor_id: UUID
    project_id: UUID
    platform_id: UUID
    status: str
    mode: str | None
    ai_disabled: bool | None
    humanization_enabled: bool
    session: ReceptionSession | None


def lock_reception_project(db: Session, project_id: UUID) -> None:
    """Acquire the project before the visitor in the final write phase."""
    if (
        db.query(Project.id)
        .filter(Project.id == project_id, Project.deleted_at.is_(None))
        .with_for_update(read=True)
        .first()
        is None
    ):
        raise changed()


def reception_binding(
    db: Session,
    visitor: Visitor,
    session_id: UUID | None,
    *,
    lock: bool = False,
) -> ReceptionBinding:
    with db.no_autoflush:
        if (
            db.query(Project.id)
            .filter(
                Project.id == visitor.project_id, Project.deleted_at.is_(None)
            )
            .first()
            is None
        ):
            raise changed()
        platform = db.query(Platform.id).filter(
            Platform.id == visitor.platform_id,
            Platform.project_id == visitor.project_id,
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
        )
        if lock:
            platform = platform.with_for_update(read=True)
        if platform.first() is None:
            raise changed()
        query = db.query(VisitorSession).filter(
            VisitorSession.visitor_id == visitor.id,
            VisitorSession.project_id == visitor.project_id,
        )
        if session_id is not None:
            query = query.filter(VisitorSession.status == "open")
        query = query.order_by(
            VisitorSession.created_at.desc(), VisitorSession.id.desc()
        ).populate_existing()
        if lock:
            query = query.with_for_update()
        current = query.first()
        if session_id is not None and (
            current is None or current.id != session_id
        ):
            raise changed()
        return ReceptionBinding(
            visitor_id=visitor.id,
            project_id=visitor.project_id,
            platform_id=visitor.platform_id,
            status=visitor.service_status,
            mode=visitor.service_mode,
            ai_disabled=visitor.ai_disabled,
            humanization_enabled=visitor.humanization_skill_enabled,
            session=ReceptionSession(
                current.id, current.status, current.staff_id
            )
            if current is not None
            else None,
        )
