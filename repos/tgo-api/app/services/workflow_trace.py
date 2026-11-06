"""Resolve a stored customer message to the original server-owned AI request."""

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_interaction_run import AIInteractionRun
from app.models.visitor_session import VisitorSession
from app.schemas.ai_workflows import WorkflowExecution
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id


def find_business_request_for_message(
    db: Session,
    project_id: UUID,
    visitor_session_id: UUID,
    source_message_id: str,
) -> UUID | None:
    """Use persisted tenant, visitor, platform and message scope, not caller identity claims."""

    session = db.scalar(
        select(VisitorSession).where(
            VisitorSession.id == visitor_session_id,
            VisitorSession.project_id == project_id,
        )
    )
    if session is None or session.platform_id is None:
        return None
    return db.scalar(
        select(AIInteractionRun.id).where(
            AIInteractionRun.project_id == project_id,
            AIInteractionRun.platform_id == session.platform_id,
            AIInteractionRun.visitor_id == session.visitor_id,
            AIInteractionRun.channel_id
            == build_visitor_channel_id(session.visitor_id),
            AIInteractionRun.channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE,
            AIInteractionRun.source_message_id == source_message_id,
        )
    )


def find_ai_request_for_channel_message(
    db: Session,
    project_id: UUID,
    channel_id: str,
    source_message_id: str,
) -> AIInteractionRun | None:
    """Require one unambiguous persisted request in this tenant and channel."""

    runs = list(
        db.scalars(
            select(AIInteractionRun)
            .where(
                AIInteractionRun.project_id == project_id,
                AIInteractionRun.channel_id == channel_id,
                AIInteractionRun.channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE,
                AIInteractionRun.source_message_id == source_message_id,
            )
            .limit(2)
        )
    )
    if len(runs) != 1 or channel_id != build_visitor_channel_id(
        runs[0].visitor_id
    ):
        return None
    return runs[0]


def workflow_records_for_ai_request(
    db: Session,
    run: AIInteractionRun,
    records: list[WorkflowExecution],
) -> list[WorkflowExecution]:
    """Exclude workflow rows whose saved session does not belong to this request's customer."""

    candidates: list[tuple[WorkflowExecution, UUID]] = []
    session_ids: set[UUID] = set()
    for record in records:
        if (
            record.business_request_id != str(run.id)
            or not record.visitor_session_id
        ):
            continue
        try:
            session_id = UUID(record.visitor_session_id)
        except ValueError:
            continue
        session_ids.add(session_id)
        candidates.append((record, session_id))
    if not session_ids:
        return []
    owned_ids = set(
        db.scalars(
            select(VisitorSession.id).where(
                VisitorSession.id.in_(session_ids),
                VisitorSession.project_id == run.project_id,
                VisitorSession.platform_id == run.platform_id,
                VisitorSession.visitor_id == run.visitor_id,
            )
        )
    )
    return [
        record for record, session_id in candidates if session_id in owned_ids
    ]


async def page_workflow_records_for_ai_request(
    db: Session,
    run: AIInteractionRun,
    limit: int,
    skip: int,
    fetch_page: Callable[[int, int], Awaitable[list[WorkflowExecution]]],
) -> list[WorkflowExecution]:
    """Page over verified rows, so foreign or malformed rows cannot hide later matches."""

    raw_skip = 0
    verified_seen = 0
    selected: list[WorkflowExecution] = []
    while len(selected) < limit:
        raw = await fetch_page(raw_skip, limit)
        if not raw:
            break
        for record in workflow_records_for_ai_request(db, run, raw):
            if verified_seen >= skip and len(selected) < limit:
                selected.append(record)
            verified_seen += 1
        raw_skip += len(raw)
        if len(raw) < limit:
            break
    return selected


async def page_workflow_records_for_ai_request_after(
    db: Session,
    run: AIInteractionRun,
    limit: int,
    before_started_at: str | None,
    before_id: str | None,
    fetch_page: Callable[
        [str | None, str | None, int], Awaitable[list[WorkflowExecution]]
    ],
) -> list[WorkflowExecution]:
    """Read verified message runs through stable raw execution pages."""
    selected: list[WorkflowExecution] = []
    cursor_at, cursor_id = before_started_at, before_id
    while len(selected) < limit:
        raw = await fetch_page(cursor_at, cursor_id, limit)
        if not raw:
            break
        selected.extend(
            workflow_records_for_ai_request(db, run, raw)[
                : limit - len(selected)
            ]
        )
        last = raw[-1]
        next_at, next_id = last.started_at.isoformat(), last.id
        if (next_at, next_id) == (cursor_at, cursor_id):
            break
        cursor_at, cursor_id = next_at, next_id
        if len(raw) < limit:
            break
    return selected
