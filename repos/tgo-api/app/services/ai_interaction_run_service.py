"""Channel-neutral exactly-once claim and lifecycle for inbound AI messages."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Visitor, Platform, Project
from app.services.visitor_access import denied as visitor_access_denied
from app.core.logging import get_logger
from app.utils.const import AI_INTERACTION_FAILURE_CODES
from app.models.ai_interaction_run import (
    AIInteractionRun,
    AIInteractionRunStatus,
)
from app.models.ai_execution_attempt import AIExecutionAttempt
from app.models.ai_execution_tool_event import AIExecutionToolEvent
from app.models.ai_execution_business_query import AIExecutionBusinessQuery

logger = get_logger(__name__)


def safe_ai_interaction_failure_code(error_message: str | None) -> str | None:
    """Keep upstream exceptions and customer content out of durable run rows."""
    if not error_message:
        return None
    if error_message in AI_INTERACTION_FAILURE_CODES:
        return error_message
    return "ai_processing_failed"


@dataclass(frozen=True)
class AIInteractionIdentity:
    """Tenant and channel coordinates that make an upstream message unique."""

    project_id: UUID
    platform_id: UUID
    visitor_id: UUID
    channel_id: str
    channel_type: int
    source_message_id: str


@dataclass(frozen=True)
class AIInteractionClaim:
    """Result of claiming an inbound message for AI processing."""

    run: AIInteractionRun
    is_duplicate: bool


def build_request_fingerprint(
    *,
    visitor_id: UUID,
    message: str,
    message_type: int,
) -> str:
    """Hash request content so key reuse cannot hide changed input."""

    canonical_payload = json.dumps(
        {
            "visitor_id": str(visitor_id),
            "message": message,
            "message_type": int(message_type),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def _find_existing(
    db: Session,
    identity: AIInteractionIdentity,
    *,
    lock: bool,
) -> AIInteractionRun | None:
    query = (
        db.query(AIInteractionRun)
        .filter(
            AIInteractionRun.project_id == identity.project_id,
            AIInteractionRun.platform_id == identity.platform_id,
            AIInteractionRun.channel_id == identity.channel_id,
            AIInteractionRun.channel_type == identity.channel_type,
            AIInteractionRun.source_message_id == identity.source_message_id,
        )
    )
    if lock:
        query = query.with_for_update()
    return query.first()


def _validate_fingerprint(
    run: AIInteractionRun,
    request_fingerprint: str,
) -> None:
    if run.request_fingerprint != request_fingerprint:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "source_message_id was already used with different "
                "message content"
            ),
        )


def _reuse_or_retry(
    db: Session,
    run: AIInteractionRun,
    request_fingerprint: str,
    response_client_msg_no: str,
) -> AIInteractionClaim:
    _validate_fingerprint(run, request_fingerprint)
    if run.status != AIInteractionRunStatus.FAILED.value:
        return AIInteractionClaim(run=run, is_duplicate=True)

    run.status = AIInteractionRunStatus.RUNNING.value
    run.response_client_msg_no = response_client_msg_no
    run.error_message = None
    run.completed_at = None
    run.updated_at = datetime.now(timezone.utc)
    db.commit()
    return AIInteractionClaim(run=run, is_duplicate=False)


def record_ai_execution_attempt(
    run_id: UUID, project_id: UUID, response_client_msg_no: str,
    execution_id: UUID,
) -> None:
    """Bind a trusted AI event to the current tenant-scoped message attempt."""
    db = SessionLocal()
    try:
        run = db.query(AIInteractionRun).filter(
            AIInteractionRun.id == run_id,
            AIInteractionRun.project_id == project_id,
            AIInteractionRun.response_client_msg_no == response_client_msg_no,
            AIInteractionRun.status == AIInteractionRunStatus.RUNNING.value,
        ).with_for_update().first()
        if run is None:
            raise ValueError("AI interaction scope changed")
        prior = db.query(AIExecutionAttempt).filter(
            AIExecutionAttempt.interaction_run_id == run_id,
            AIExecutionAttempt.response_client_msg_no == response_client_msg_no,
        ).with_for_update().first()
        if prior is not None:
            if prior.execution_id != execution_id:
                raise ValueError("AI execution identity changed")
            return
        db.add(AIExecutionAttempt(
            execution_id=execution_id, project_id=project_id,
            interaction_run_id=run_id,
            response_client_msg_no=response_client_msg_no,
            status="running",
        ))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("AI execution identity already used") from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def record_ai_tool_event(
    run_id: UUID, project_id: UUID, response_client_msg_no: str,
    execution_id: UUID, sequence_no: int, tool_name: str,
    tool_call_id: str | None, status: Literal["started", "completed", "failed"],
) -> None:
    """Store trusted tool metadata only while this reply attempt is active."""
    if (
        isinstance(sequence_no, bool) or not isinstance(sequence_no, int)
        or sequence_no < 1 or status not in {"started", "completed", "failed"}
        or not isinstance(tool_name, str) or not tool_name.strip()
        or len(tool_name) > 200 or not tool_name.isprintable()
        or (tool_call_id is not None and (
            not isinstance(tool_call_id, str) or not tool_call_id.strip()
            or len(tool_call_id) > 128 or not tool_call_id.isprintable()
        ))
    ):
        raise ValueError("Invalid AI tool event")
    db = SessionLocal()
    try:
        run = db.query(AIInteractionRun).filter(
            AIInteractionRun.id == run_id,
            AIInteractionRun.project_id == project_id,
            AIInteractionRun.response_client_msg_no == response_client_msg_no,
            AIInteractionRun.status == AIInteractionRunStatus.RUNNING.value,
        ).with_for_update().first()
        attempt = db.query(AIExecutionAttempt).filter(
            AIExecutionAttempt.execution_id == execution_id,
            AIExecutionAttempt.project_id == project_id,
            AIExecutionAttempt.interaction_run_id == run_id,
            AIExecutionAttempt.response_client_msg_no == response_client_msg_no,
            AIExecutionAttempt.status == "running",
        ).with_for_update().first() if run is not None else None
        if run is None or attempt is None:
            raise ValueError("AI tool event scope changed")
        prior = db.query(AIExecutionToolEvent).filter(
            AIExecutionToolEvent.execution_id == execution_id,
            AIExecutionToolEvent.sequence_no == sequence_no,
        ).with_for_update().first()
        if prior is not None:
            if (
                prior.tool_name != tool_name or prior.tool_call_id != tool_call_id
                or prior.status != status
            ):
                raise ValueError("AI tool event changed")
            return
        db.add(AIExecutionToolEvent(
            project_id=project_id, execution_id=execution_id,
            sequence_no=sequence_no, tool_name=tool_name,
            tool_call_id=tool_call_id, status=status,
        ))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("AI tool event already used") from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_ai_tool_events(
    db: Session, project_id: UUID, execution_id: UUID,
    limit: int, skip: int,
) -> list[AIExecutionToolEvent]:
    """Only the owner of the saved attempt may inspect its tool metadata."""
    owned = db.scalar(select(AIExecutionAttempt.execution_id).where(
        AIExecutionAttempt.execution_id == execution_id,
        AIExecutionAttempt.project_id == project_id,
    ))
    if owned is None:
        return []
    return list(db.scalars(select(AIExecutionToolEvent).where(
        AIExecutionToolEvent.project_id == project_id,
        AIExecutionToolEvent.execution_id == execution_id,
    ).order_by(
        AIExecutionToolEvent.sequence_no,
    ).limit(limit).offset(skip)))


def record_ai_business_query(
    run_id: UUID, project_id: UUID, response_client_msg_no: str,
    execution_id: UUID, query_id: UUID, operation: str,
    outcome: Literal["passed", "failed"],
) -> None:
    """Bind server-owned query metadata to the active customer reply attempt."""
    if operation not in {
        "order_query", "logistics_query", "customer_profile_query", "membership_query",
    } or outcome not in {"passed", "failed"}:
        raise ValueError("Invalid AI business query")
    db = SessionLocal()
    try:
        run = db.query(AIInteractionRun).filter(
            AIInteractionRun.id == run_id,
            AIInteractionRun.project_id == project_id,
            AIInteractionRun.response_client_msg_no == response_client_msg_no,
            AIInteractionRun.status == AIInteractionRunStatus.RUNNING.value,
        ).with_for_update().first()
        attempt = db.query(AIExecutionAttempt).filter(
            AIExecutionAttempt.execution_id == execution_id,
            AIExecutionAttempt.project_id == project_id,
            AIExecutionAttempt.interaction_run_id == run_id,
            AIExecutionAttempt.response_client_msg_no == response_client_msg_no,
            AIExecutionAttempt.status == "running",
        ).with_for_update().first() if run is not None else None
        if run is None or attempt is None:
            raise ValueError("AI business query scope changed")
        prior = db.query(AIExecutionBusinessQuery).filter(
            AIExecutionBusinessQuery.query_id == query_id,
        ).with_for_update().first()
        if prior is not None:
            if (prior.project_id != project_id or prior.execution_id != execution_id
                    or prior.operation != operation or prior.outcome != outcome):
                raise ValueError("AI business query changed")
            return
        db.add(AIExecutionBusinessQuery(
            query_id=query_id, project_id=project_id, execution_id=execution_id,
            operation=operation, outcome=outcome,
        ))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("AI business query already used") from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_ai_business_queries(
    db: Session, project_id: UUID, execution_id: UUID,
    limit: int, skip: int,
) -> list[AIExecutionBusinessQuery]:
    """Only the attempt owner may resolve its formal query audit IDs."""
    owned = db.scalar(select(AIExecutionAttempt.execution_id).where(
        AIExecutionAttempt.execution_id == execution_id,
        AIExecutionAttempt.project_id == project_id,
    ))
    if owned is None:
        return []
    return list(db.scalars(select(AIExecutionBusinessQuery).where(
        AIExecutionBusinessQuery.project_id == project_id,
        AIExecutionBusinessQuery.execution_id == execution_id,
    ).order_by(
        AIExecutionBusinessQuery.created_at, AIExecutionBusinessQuery.query_id,
    ).limit(limit).offset(skip)))


def claim_ai_interaction(
    db: Session,
    *,
    identity: AIInteractionIdentity,
    request_fingerprint: str,
    response_client_msg_no: str,
) -> AIInteractionClaim:
    """Atomically claim a message, or return the already claimed AI run."""

    current = db.scalar(
        select(Visitor).join(Platform, Visitor.platform_id == Platform.id)
        .join(Project, Visitor.project_id == Project.id).where(
            Visitor.id == identity.visitor_id, Visitor.project_id == identity.project_id,
            Visitor.platform_id == identity.platform_id, Visitor.deleted_at.is_(None),
            Visitor.access_retired_at.is_(None), Platform.project_id == identity.project_id,
            Platform.is_active.is_(True), Platform.deleted_at.is_(None), Project.deleted_at.is_(None),
        ).with_for_update(of=Visitor).execution_options(populate_existing=True)
    )
    if current is None:
        raise visitor_access_denied()

    existing = _find_existing(db, identity, lock=True)
    if existing is not None:
        return _reuse_or_retry(
            db, existing, request_fingerprint, response_client_msg_no
        )

    run = AIInteractionRun(
        project_id=identity.project_id,
        platform_id=identity.platform_id,
        visitor_id=identity.visitor_id,
        channel_id=identity.channel_id,
        channel_type=identity.channel_type,
        source_message_id=identity.source_message_id,
        request_fingerprint=request_fingerprint,
        response_client_msg_no=response_client_msg_no,
        status=AIInteractionRunStatus.RUNNING.value,
    )
    db.add(run)
    try:
        db.flush()
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _find_existing(db, identity, lock=True)
        if existing is None:
            raise
        return _reuse_or_retry(
            db, existing, request_fingerprint, response_client_msg_no
        )
    return AIInteractionClaim(run=run, is_duplicate=False)


def mark_ai_interaction_finished(
    run_id: UUID,
    *,
    response_client_msg_no: str,
    error_message: str | None = None,
) -> None:
    """Finish a run from async code using an independent session."""

    failure_code = safe_ai_interaction_failure_code(error_message)
    db = SessionLocal()
    try:
        run = (
            db.query(AIInteractionRun)
            .filter(AIInteractionRun.id == run_id)
            .with_for_update()
            .first()
        )
        if run is None:
            logger.warning(
                "AI interaction run was not found while updating lifecycle",
                extra={"run_id": str(run_id)},
            )
            return
        if run.response_client_msg_no != response_client_msg_no:
            return  # An old attempt cannot finish a newer retry.
        attempt = db.query(AIExecutionAttempt).filter(
            AIExecutionAttempt.interaction_run_id == run_id,
            AIExecutionAttempt.response_client_msg_no == response_client_msg_no,
        ).with_for_update().first()
        if run.error_message == "visitor_access_ended":
            if attempt is not None and attempt.status == "running":
                attempt.status = "failed"
                attempt.completed_at = datetime.now(timezone.utc)
                db.commit()
            return  # A late task cannot erase durable retirement or permit retry.
        if run.status != AIInteractionRunStatus.RUNNING.value:
            return  # A second callback cannot rewrite a terminal outcome.
        run.status = (
            AIInteractionRunStatus.FAILED.value
            if failure_code
            else AIInteractionRunStatus.COMPLETED.value
        )
        run.error_message = failure_code
        run.completed_at = datetime.now(timezone.utc)
        run.updated_at = datetime.now(timezone.utc)
        if attempt is not None and attempt.status == "running":
            attempt.status = run.status
            attempt.completed_at = run.completed_at
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to update AI interaction lifecycle",
            extra={"run_id": str(run_id)},
        )
    finally:
        db.close()
