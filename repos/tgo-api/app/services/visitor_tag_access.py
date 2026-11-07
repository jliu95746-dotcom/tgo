"Serialized customer tag changes using the original staff and target \
versions."

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.models import Staff, Tag, Visitor, VisitorTag
from app.services.profile_access import OperationGuard, not_found
from app.services.visitor_chat_access import authority_database
from app.services.visitor_mutation_access import (
    VisitorMutationScope,
    begin_mutation,
    changed,
    timestamp,
)


@dataclass(frozen=True)
class TagChangeScope:
    customer: VisitorMutationScope
    tag_id: str
    tag_version: datetime
    remove: bool = False
    association_id: UUID | None = None
    association_version: datetime | None = None
    completed: bool = False

    def check(
        self, db: Session, *, lock: bool = False
    ) -> tuple[Visitor, Tag, VisitorTag | None]:
        with db.no_autoflush:
            visitor = self.customer.check(db, lock=lock)
            query = (
                db.query(Tag)
                .filter(
                    Tag.id == self.tag_id,
                    Tag.project_id == self.customer.access.project_id,
                    Tag.deleted_at.is_(None),
                )
                .populate_existing()
            )
            if lock:
                query = query.with_for_update(read=True)
            tag: Tag | None = query.first()
            if tag is None:
                raise not_found()
            if timestamp(tag.updated_at) != self.tag_version:
                raise changed()
            links = (
                db.query(VisitorTag)
                .filter(
                    VisitorTag.project_id == self.customer.access.project_id,
                    VisitorTag.visitor_id == self.customer.visitor_id,
                    VisitorTag.tag_id == self.tag_id,
                )
                .populate_existing()
            )
            if lock:
                links = links.with_for_update()
            link: VisitorTag | None = links.first()
            if self.completed and self.remove:
                if link is not None:
                    raise changed()
            elif self.remove or self.completed:
                if (
                    link is None
                    or link.deleted_at is not None
                    or link.id != self.association_id
                    or timestamp(link.updated_at) != self.association_version
                ):
                    raise changed()
            elif link is not None:
                # A retired association is not implicitly restored.
                if link.deleted_at is not None:
                    raise changed()
                raise HTTPException(
                    400, "Visitor-tag association already exists"
                )
            return visitor, tag, link

    def guard(self) -> OperationGuard:
        def check() -> None:
            with authority_database() as db:
                self.check(db)

        return OperationGuard(check)


def begin_tag_change(
    db: Session,
    credentials: HTTPAuthorizationCredentials | None,
    actor: Staff,
    visitor_id: UUID,
    tag_id: str,
    *,
    remove: bool = False,
) -> TagChangeScope:
    customer = begin_mutation(
        db,
        credentials,
        actor,
        visitor_id,
        "tags:delete" if remove else "tags:create",
    )
    tag = (
        db.query(Tag)
        .filter(
            Tag.id == tag_id,
            Tag.project_id == customer.access.project_id,
            Tag.deleted_at.is_(None),
        )
        .populate_existing()
        .first()
    )
    if tag is None:
        raise not_found()
    link = None
    if remove:
        link = (
            db.query(VisitorTag)
            .filter(
                VisitorTag.visitor_id == visitor_id,
                VisitorTag.tag_id == tag_id,
                VisitorTag.project_id == customer.access.project_id,
                VisitorTag.deleted_at.is_(None),
            )
            .populate_existing()
            .first()
        )
        if link is None:
            raise not_found()
    scope = TagChangeScope(
        customer,
        tag_id,
        timestamp(tag.updated_at),
        remove,
        link.id if link is not None else None,
        timestamp(link.updated_at) if link is not None else None,
    )
    scope.check(db)
    return scope


def begin_tag_removal(
    db: Session,
    credentials: HTTPAuthorizationCredentials | None,
    actor: Staff,
    association_id: UUID,
) -> TagChangeScope:
    link = (
        db.query(VisitorTag)
        .filter(
            VisitorTag.id == association_id,
            VisitorTag.project_id == actor.project_id,
            VisitorTag.deleted_at.is_(None),
        )
        .populate_existing()
        .first()
    )
    if link is None:
        raise not_found()
    # Keep the path association identity, even if it is replaced while
    # preparing.
    scope = begin_tag_change(
        db, credentials, actor, link.visitor_id, link.tag_id, remove=True
    )
    if scope.association_id != association_id:
        raise changed()
    return scope


def commit_tag_change(
    db: Session, scope: TagChangeScope
) -> tuple[VisitorTag | None, TagChangeScope]:
    try:
        if scope.completed:
            raise changed()
        visitor, _, link = scope.check(db, lock=True)
        if scope.remove:
            assert link is not None
            db.delete(link)
        else:
            link = VisitorTag(
                project_id=scope.customer.access.project_id,
                visitor_id=scope.customer.visitor_id,
                tag_id=scope.tag_id,
            )
            db.add(link)
        written_version = max(
            datetime.utcnow(),
            scope.customer.original_version + timedelta(microseconds=1),
        )
        visitor.updated_at = written_version
        db.flush()
        output = replace(
            scope,
            completed=True,
            customer=replace(scope.customer, original_version=written_version),
            association_id=link.id,
            association_version=timestamp(link.updated_at),
        )
        db.commit()
        if not scope.remove:
            db.refresh(link)
        return None if scope.remove else link, output
    except BaseException:
        db.rollback()
        raise
