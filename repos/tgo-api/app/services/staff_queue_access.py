"""Original staff queue commands tied to one wait and its exact session."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.models import Staff, VisitorSession, VisitorWaitingQueue
from app.services.profile_access import OperationGuard, not_found
from app.services.queue_lifecycle import cancel_entry
from app.services.visitor_chat_access import authority_database
from app.services.visitor_mutation_access import (
    VisitorMutationScope,
    begin_mutation,
    changed,
    timestamp,
)


@dataclass(frozen=True)
class StaffQueueScope:
    customer: VisitorMutationScope
    entry_id: UUID
    queue_version: datetime
    session_id: UUID
    session_version: datetime
    status: str = "waiting"
    staff_id: UUID | None = None
    accepting: bool = True

    def check(
        self, db: Session, *, lock: bool = False
    ) -> tuple[VisitorWaitingQueue, VisitorSession]:
        with db.no_autoflush:
            visitor = self.customer.check(db, lock=lock)
            query = (
                db.query(VisitorWaitingQueue)
                .filter(
                    VisitorWaitingQueue.id == self.entry_id,
                    VisitorWaitingQueue.visitor_id == self.customer.visitor_id,
                    VisitorWaitingQueue.project_id
                    == self.customer.access.project_id,
                    VisitorWaitingQueue.session_id == self.session_id,
                )
                .populate_existing()
            )
            if lock:
                query = query.with_for_update()
            entry: VisitorWaitingQueue | None = query.first()
            if entry is None:
                raise not_found()
            if (
                entry.status != self.status
                or timestamp(entry.updated_at) != self.queue_version
                or entry.assigned_staff_id != self.staff_id
            ):
                raise changed()
            if self.status == "waiting":
                if visitor.service_status != "queued" or (
                    self.accepting
                    and entry.expired_at is not None
                    and timestamp(entry.expired_at) <= datetime.utcnow()
                ):
                    raise changed()
            sessions = (
                db.query(VisitorSession)
                .filter(
                    VisitorSession.project_id
                    == self.customer.access.project_id,
                    VisitorSession.visitor_id == self.customer.visitor_id,
                )
                .order_by(
                    VisitorSession.created_at.desc(), VisitorSession.id.desc()
                )
                .populate_existing()
            )
            if lock:
                sessions = sessions.with_for_update()
            session: VisitorSession | None = sessions.first()
            if (
                session is None
                or session.id != self.session_id
                or session.platform_id != self.customer.platform_id
                or session.staff_id != self.staff_id
                or session.status
                != ("closed" if self.status == "cancelled" else "open")
                or timestamp(session.updated_at) != self.session_version
            ):
                raise changed()
            return entry, session

    def guard(self) -> OperationGuard:
        def check() -> None:
            with authority_database() as db:
                self.check(db)

        return OperationGuard(check)

    def completed(
        self,
        customer: VisitorMutationScope,
        entry: VisitorWaitingQueue,
        session: VisitorSession,
    ) -> "StaffQueueScope":
        return replace(
            self,
            customer=customer,
            queue_version=timestamp(entry.updated_at),
            session_version=timestamp(session.updated_at),
            status=entry.status,
            staff_id=entry.assigned_staff_id,
        )


def begin_queue_change(
    db: Session,
    credentials: HTTPAuthorizationCredentials | None,
    actor: Staff,
    entry_id: UUID,
    *,
    accepting: bool = True,
) -> StaffQueueScope:
    entry = (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.id == entry_id,
            VisitorWaitingQueue.project_id == actor.project_id,
        )
        .populate_existing()
        .first()
    )
    if entry is None:
        raise not_found()
    customer = begin_mutation(
        db, credentials, actor, entry.visitor_id, "visitors:update", claim=True
    )
    session = (
        db.query(VisitorSession)
        .filter(
            VisitorSession.id == entry.session_id,
            VisitorSession.project_id == customer.access.project_id,
            VisitorSession.visitor_id == customer.visitor_id,
        )
        .populate_existing()
        .first()
    )
    if session is None:
        raise not_found()
    scope = StaffQueueScope(
        customer,
        entry.id,
        timestamp(entry.updated_at),
        session.id,
        timestamp(session.updated_at),
        accepting=accepting,
    )
    scope.check(db)
    return scope


def commit_queue_cancel(
    db: Session, scope: StaffQueueScope, reason: str | None
) -> StaffQueueScope:
    try:
        if scope.accepting or scope.status != "waiting":
            raise changed()
        entry, session = scope.check(db, lock=True)
        visitor = scope.customer.check(db)
        if not cancel_entry(
            db, scope.entry_id, scope.customer.access.project_id, reason
        ):
            raise changed()
        # Match ownership/version snapshots to this commit, not a later refresh.
        visitor.updated_at = max(
            datetime.utcnow(),
            scope.customer.original_version + timedelta(microseconds=1),
        )
        db.flush()
        customer = replace(
            scope.customer.after_commit(visitor), owner_required=False
        )
        output = scope.completed(customer, entry, session)
        db.commit()
        return output
    except BaseException:
        db.rollback()
        raise
