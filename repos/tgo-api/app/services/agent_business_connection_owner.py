"""Fresh metadata only: credential values never leave their API owner."""
from datetime import UTC, datetime
from collections.abc import Sequence
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.schemas.agent_business_connections import (
    BusinessConnectionOwner,
    BusinessOperation,
    ConnectionOwnerQuery,
    ConnectionOperationConfiguration,
)
from app.models.business_connection import BusinessConnection
from app.models.business_connection_test import BusinessConnectionTest
from app.services.business_connection_proofs import assess_report, utc
from app.services.agent_activation_requirements import require_activation_actor
from app.services.business_connections import get
from app.core.exceptions import NotFoundError

_AGENT_SUPPORTED_OPERATIONS = frozenset(
    {
        "order_query",
        "logistics_query",
        "customer_profile_query",
        "membership_query",
    }
)


def supported_operations(
    capabilities: Sequence[str],
) -> list[BusinessOperation]:
    return [
        cast(BusinessOperation, operation)
        for operation in capabilities
        if operation in _AGENT_SUPPORTED_OPERATIONS
    ]


def operation_configuration(
    db: Session, project: UUID, connection_id: UUID
) -> list[ConnectionOperationConfiguration]:
    connection = db.get(
        BusinessConnection, connection_id, populate_existing=True
    )
    if connection is None or connection.project_id != project:
        raise NotFoundError("连接")
    result: list[ConnectionOperationConfiguration] = []
    for operation in supported_operations(connection.capabilities):
        row: BusinessConnectionTest | None = db.scalar(
            select(BusinessConnectionTest)
            .where(
                BusinessConnectionTest.project_id == project,
                BusinessConnectionTest.connection_id == connection_id,
                BusinessConnectionTest.operation == operation,
            )
            .order_by(
                BusinessConnectionTest.created_at.desc(),
                BusinessConnectionTest.id.desc(),
            )
            .limit(1)
            .execution_options(populate_existing=True)
        )
        if row is None:
            result.append(
                ConnectionOperationConfiguration(
                    operation=operation,
                    report_id=None,
                    status="not_tested",
                    valid_until=None,
                    issues=[],
                )
            )
            continue
        if (
            row.status == "running"
            and row.configuration_revision == connection.configuration_revision
        ):
            if utc(row.expires_at) <= datetime.now(UTC):
                result.append(
                    ConnectionOperationConfiguration(
                        operation=operation,
                        report_id=row.id,
                        status="unavailable",
                        valid_until=None,
                        issues=["evidence_unknown"],
                    )
                )
                continue
            result.append(
                ConnectionOperationConfiguration(
                    operation=operation,
                    report_id=row.id,
                    status="running",
                    valid_until=None,
                    issues=[],
                )
            )
            continue
        assessment = assess_report(db, row, connection, latest_checked=True)
        result.append(
            ConnectionOperationConfiguration(
                operation=operation,
                report_id=row.id,
                status=assessment.status,
                valid_until=assessment.valid_until,
                issues=assessment.issues,
            )
        )
    return result


def current_business_connection(
    db: Session, query: ConnectionOwnerQuery
) -> BusinessConnectionOwner:
    require_activation_actor(db, query.project_id, query.requested_by)
    connection = get(db, query.project_id, query.connection_id)
    return BusinessConnectionOwner(
        **query.model_dump(),
        name=connection.name,
        mode=connection.mode,
        enabled=connection.enabled,
        operations=supported_operations(connection.capabilities),
        revision=connection.revision,
        configuration_revision=connection.configuration_revision,
        authorization_revision=connection.authorization_revision,
        resource_revision=connection.resource_revision,
        test_status=connection.test_status,
        configuration_tests=operation_configuration(
            db, query.project_id, query.connection_id
        ),
        checked_at=datetime.now(UTC),
    )
