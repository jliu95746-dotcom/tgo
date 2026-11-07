"Only latest, unexpired, individually authorized operation reports are \
proof."

from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.business_connection import BusinessConnection
from app.models.business_connection_test import BusinessConnectionTest
from app.models.staff import Staff
from app.models.project import Project
from app.schemas.connection_test_protocol import ConnectionTestReceipt
from app.schemas.business_connection_tests import (
    BusinessConnectionTestApplicability,
    ApplicabilityIssue,
)
from app.services.business_connection_vault import CredentialVault
from app.core.exceptions import TGOAPIException


def utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )


def parsed_receipt(
    row: BusinessConnectionTest,
) -> ConnectionTestReceipt | None:
    if row.receipt is None:
        return None
    try:
        result = ConnectionTestReceipt.model_validate(row.receipt)
    except ValidationError:
        return None
    identity = (
        "project_id",
        "actor_id",
        "connection_id",
        "expected_revision",
        "configuration_revision",
        "authorization_revision",
        "resource_revision",
        "request_id",
        "operation",
        "mode",
    )
    return (
        result
        if all(
            getattr(result, field) == getattr(row, field) for field in identity
        )
        else None
    )


def assess_report(
    db: Session,
    row: BusinessConnectionTest,
    connection: BusinessConnection,
    *,
    latest_checked: bool = False,
) -> BusinessConnectionTestApplicability:
    now = datetime.now(UTC)
    receipt = parsed_receipt(row)
    if row.mode == "demo":
        return BusinessConnectionTestApplicability(
            status="not_passed",
            checked_at=now,
            valid_until=None,
            issues=["demo_only"],
        )
    if row.status != "completed" or (
        receipt is not None and receipt.outcome != "passed"
    ):
        return BusinessConnectionTestApplicability(
            status="not_passed",
            checked_at=now,
            valid_until=None,
            issues=["test_not_passed"],
        )
    if receipt is None or row.completed_at is None:
        return BusinessConnectionTestApplicability(
            status="unavailable",
            checked_at=now,
            valid_until=None,
            issues=["evidence_unknown"],
        )
    valid_until = min(receipt.checked_at, utc(row.completed_at)) + timedelta(
        seconds=settings.BUSINESS_CONNECTION_TEST_TTL_SECONDS
    )
    if (
        receipt.checked_at > now + timedelta(seconds=5)
        or receipt.checked_at < utc(row.created_at) - timedelta(seconds=5)
        or receipt.checked_at > utc(row.completed_at) + timedelta(seconds=5)
    ):
        return BusinessConnectionTestApplicability(
            status="unavailable",
            checked_at=now,
            valid_until=valid_until,
            issues=["evidence_unknown"],
        )
    issues: list[ApplicabilityIssue] = []
    if (
        row.configuration_revision != connection.configuration_revision
        or row.mode != connection.mode
    ):
        issues.append("configuration_changed")
    if row.operation not in connection.capabilities:
        issues.append("capability_removed")
    if not latest_checked and not issues:
        latest = db.scalar(
            select(BusinessConnectionTest.id)
            .where(
                BusinessConnectionTest.project_id == connection.project_id,
                BusinessConnectionTest.connection_id == connection.id,
                BusinessConnectionTest.configuration_revision
                == connection.configuration_revision,
                BusinessConnectionTest.operation == row.operation,
            )
            .order_by(
                BusinessConnectionTest.created_at.desc(),
                BusinessConnectionTest.id.desc(),
            )
            .limit(1)
        )
        if latest != row.id:
            issues.append("superseded")
    if valid_until <= now:
        issues.append("expired")
    actor = (
        db.scalar(
            select(Staff)
            .where(
                Staff.id == row.actor_id,
                Staff.project_id == connection.project_id,
            )
            .execution_options(populate_existing=True)
        )
        if row.actor_id is not None
        else None
    )
    project = db.get(Project, connection.project_id, populate_existing=True)
    if (
        actor is None
        or actor.role != "admin"
        or not actor.account_enabled
        or actor.deleted_at is not None
        or project is None
        or project.deleted_at is not None
    ):
        issues.append("reviewer_unavailable")
    if connection.credential_ciphertext:
        try:
            CredentialVault().decrypt(
                connection.credential_ciphertext,
                connection.project_id,
                connection.id,
            )
        except TGOAPIException:
            issues.append("credential_unavailable")
    return BusinessConnectionTestApplicability(
        status="unavailable"
        if "credential_unavailable" in issues
        else "stale"
        if issues
        else "current",
        checked_at=now,
        valid_until=valid_until,
        issues=issues,
    )


def current_capabilities(
    db: Session, connection: BusinessConnection
) -> list[str]:
    if connection.mode != "http":
        return []
    verified = []
    for operation in connection.capabilities:
        row = db.scalar(
            select(BusinessConnectionTest)
            .where(
                BusinessConnectionTest.project_id == connection.project_id,
                BusinessConnectionTest.connection_id == connection.id,
                BusinessConnectionTest.configuration_revision
                == connection.configuration_revision,
                BusinessConnectionTest.operation == operation,
            )
            .order_by(
                BusinessConnectionTest.created_at.desc(),
                BusinessConnectionTest.id.desc(),
            )
            .limit(1)
            .execution_options(populate_existing=True)
        )
        if (
            row is None
            or row.status != "completed"
            or row.actor_id is None
            or row.completed_at is None
        ):
            continue
        if (
            assess_report(db, row, connection, latest_checked=True).status
            == "current"
        ):
            verified.append(operation)
    return verified
