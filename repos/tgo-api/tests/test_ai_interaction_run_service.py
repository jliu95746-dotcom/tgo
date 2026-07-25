"""Contract tests for channel-neutral AI interaction idempotency."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models import AIInteractionRun, AIInteractionRunStatus
from app.services.ai_interaction_run_service import (
    AIInteractionIdentity,
    build_request_fingerprint,
    claim_ai_interaction,
)


def _identity() -> AIInteractionIdentity:
    return AIInteractionIdentity(
        project_id=uuid4(),
        platform_id=uuid4(),
        visitor_id=uuid4(),
        channel_id="visitor-channel",
        channel_type=251,
        source_message_id="upstream-message-1",
    )


def test_ai_interaction_run_has_global_message_identity_constraint() -> None:
    constraints = {
        constraint.name
        for constraint in AIInteractionRun.__table__.constraints
        if constraint.name is not None
    }

    assert "uq_ai_interaction_global_message" in constraints


def test_claim_ai_interaction_creates_running_record() -> None:
    db = MagicMock()
    query = db.query.return_value.filter.return_value
    query.with_for_update.return_value.first.return_value = None
    identity = _identity()
    fingerprint = build_request_fingerprint(
        visitor_id=identity.visitor_id,
        message="where is my order",
        message_type=1,
    )

    result = claim_ai_interaction(
        db,
        identity=identity,
        request_fingerprint=fingerprint,
        response_client_msg_no="ai_response_1",
    )

    assert result.is_duplicate is False
    assert result.run.status == AIInteractionRunStatus.RUNNING.value
    assert result.run.source_message_id == identity.source_message_id
    db.add.assert_called_once_with(result.run)
    db.flush.assert_called_once_with()
    db.commit.assert_called_once_with()


def test_claim_ai_interaction_reuses_matching_existing_record() -> None:
    identity = _identity()
    fingerprint = build_request_fingerprint(
        visitor_id=identity.visitor_id,
        message="where is my order",
        message_type=1,
    )
    existing = SimpleNamespace(
        request_fingerprint=fingerprint,
        response_client_msg_no="ai_existing",
        status=AIInteractionRunStatus.COMPLETED.value,
    )
    db = MagicMock()
    query = db.query.return_value.filter.return_value
    query.with_for_update.return_value.first.return_value = existing

    result = claim_ai_interaction(
        db,
        identity=identity,
        request_fingerprint=fingerprint,
        response_client_msg_no="ai_new",
    )

    assert result.is_duplicate is True
    assert result.run is existing
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_claim_ai_interaction_retries_failed_record_once() -> None:
    identity = _identity()
    fingerprint = build_request_fingerprint(
        visitor_id=identity.visitor_id,
        message="where is my order",
        message_type=1,
    )
    existing = SimpleNamespace(
        request_fingerprint=fingerprint,
        response_client_msg_no="ai_existing",
        status=AIInteractionRunStatus.FAILED.value,
        error_message="provider timeout",
        completed_at=object(),
        updated_at=None,
    )
    db = MagicMock()
    query = db.query.return_value.filter.return_value
    query.with_for_update.return_value.first.return_value = existing

    result = claim_ai_interaction(
        db,
        identity=identity,
        request_fingerprint=fingerprint,
        response_client_msg_no="ai_new",
    )

    assert result.is_duplicate is False
    assert result.run is existing
    assert existing.status == AIInteractionRunStatus.RUNNING.value
    assert existing.error_message is None
    assert existing.completed_at is None
    db.commit.assert_called_once_with()


def test_claim_rejects_reused_id_with_different_content() -> None:
    identity = _identity()
    existing = SimpleNamespace(
        request_fingerprint=build_request_fingerprint(
            visitor_id=identity.visitor_id,
            message="first payload",
            message_type=1,
        ),
        response_client_msg_no="ai_existing",
        status=AIInteractionRunStatus.RUNNING.value,
    )
    db = MagicMock()
    query = db.query.return_value.filter.return_value
    query.with_for_update.return_value.first.return_value = existing

    with pytest.raises(HTTPException) as exc_info:
        claim_ai_interaction(
            db,
            identity=identity,
            request_fingerprint=build_request_fingerprint(
                visitor_id=identity.visitor_id,
                message="different payload",
                message_type=1,
            ),
            response_client_msg_no="ai_new",
        )

    assert exc_info.value.status_code == 409
