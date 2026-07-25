"""Channel-neutral exactly-once claim and lifecycle for inbound AI messages."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models.ai_interaction_run import (
    AIInteractionRun,
    AIInteractionRunStatus,
)

logger = get_logger(__name__)


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
) -> AIInteractionClaim:
    _validate_fingerprint(run, request_fingerprint)
    if run.status != AIInteractionRunStatus.FAILED.value:
        return AIInteractionClaim(run=run, is_duplicate=True)

    run.status = AIInteractionRunStatus.RUNNING.value
    run.error_message = None
    run.completed_at = None
    run.updated_at = datetime.now(timezone.utc)
    db.commit()
    return AIInteractionClaim(run=run, is_duplicate=False)


def claim_ai_interaction(
    db: Session,
    *,
    identity: AIInteractionIdentity,
    request_fingerprint: str,
    response_client_msg_no: str,
) -> AIInteractionClaim:
    """Atomically claim a message, or return the already claimed AI run."""

    existing = _find_existing(db, identity, lock=True)
    if existing is not None:
        return _reuse_or_retry(db, existing, request_fingerprint)

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
        return _reuse_or_retry(db, existing, request_fingerprint)
    return AIInteractionClaim(run=run, is_duplicate=False)


def mark_ai_interaction_finished(
    run_id: UUID,
    *,
    error_message: str | None = None,
) -> None:
    """Finish a run from async code using an independent session."""

    db = SessionLocal()
    try:
        run = (
            db.query(AIInteractionRun)
            .filter(AIInteractionRun.id == run_id)
            .first()
        )
        if run is None:
            logger.warning(
                "AI interaction run was not found while updating lifecycle",
                extra={"run_id": str(run_id)},
            )
            return
        run.status = (
            AIInteractionRunStatus.FAILED.value
            if error_message
            else AIInteractionRunStatus.COMPLETED.value
        )
        run.error_message = error_message
        run.completed_at = datetime.now(timezone.utc)
        run.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to update AI interaction lifecycle",
            extra={"run_id": str(run_id)},
        )
    finally:
        db.close()
