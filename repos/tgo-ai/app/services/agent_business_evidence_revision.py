"Bind a confirmation/run to exact local evidence; a digest is never a \
grant."

from datetime import UTC, datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError
from app.models.agent_business_connection import AgentBusinessConnection
from app.models.agent_business_review import AgentBusinessReview
from app.models.agent_business_trial import AgentBusinessTrial
from app.services.agent_trial_fingerprint import private_digest
from app.services.agent_device_script_selection_identity import (
    selection_evidence_revision,
)


def _time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (
        value.replace(tzinfo=UTC).isoformat()
        if value.tzinfo is None
        else value.astimezone(UTC).isoformat()
    )


async def business_evidence_revision(
    db: AsyncSession,
    project: UUID,
    actor: UUID,
    agent: UUID,
) -> str | None:
    connection = await _connection_evidence_revision(db, project, actor, agent)
    script = await selection_evidence_revision(db, project, actor, agent)
    if script is None:
        return connection
    return private_digest(
        {
            "purpose": "employee-business-evidence-with-script-v1",
            "project": str(project),
            "actor": str(actor),
            "agent": str(agent),
            "connections": connection,
            "device_script": script,
        }
    )


async def _connection_evidence_revision(
    db: AsyncSession,
    project: UUID,
    actor: UUID,
    agent: UUID,
) -> str | None:
    bindings = list(
        (
            await db.scalars(
                select(AgentBusinessConnection)
                .where(
                    AgentBusinessConnection.project_id == project,
                    AgentBusinessConnection.agent_id == agent,
                    AgentBusinessConnection.enabled.is_(True),
                    AgentBusinessConnection.deleted_at.is_(None),
                )
                .order_by(AgentBusinessConnection.connection_id)
                .limit(201)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    if not bindings:
        return None
    if (
        len(bindings) > 200
        or sum(len(row.operations) for row in bindings) > 200
    ):
        raise ConflictError("当前员工关联过多，请整理业务连接后核验")
    if any(
        not row.operations
        or len(set(row.operations)) != len(row.operations)
        or set(row.operations) - {"order_query", "logistics_query"}
        for row in bindings
    ):
        raise ConflictError("当前员工的业务操作范围无法核验")
    # Three bounded reads, independent of how many historical tests exist.
    latest_tests = (
        select(
            AgentBusinessTrial.id,
            func.row_number()
            .over(
                partition_by=(
                    AgentBusinessTrial.connection_id,
                    AgentBusinessTrial.operation,
                ),
                order_by=(
                    AgentBusinessTrial.created_at.desc(),
                    AgentBusinessTrial.id.desc(),
                ),
            )
            .label("rank"),
        )
        .where(
            AgentBusinessTrial.project_id == project,
            AgentBusinessTrial.actor_id == actor,
            AgentBusinessTrial.agent_id == agent,
            AgentBusinessTrial.connection_id.in_(
                [row.connection_id for row in bindings]
            ),
            AgentBusinessTrial.deleted_at.is_(None),
        )
        .subquery()
    )
    tests = list(
        (
            await db.scalars(
                select(AgentBusinessTrial)
                .join(
                    latest_tests,
                    latest_tests.c.id == AgentBusinessTrial.id,
                )
                .where(latest_tests.c.rank == 1)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    latest_reviews = (
        select(
            AgentBusinessReview.id,
            func.row_number()
            .over(
                partition_by=AgentBusinessReview.report_id,
                order_by=(
                    AgentBusinessReview.revision.desc(),
                    AgentBusinessReview.id.desc(),
                ),
            )
            .label("rank"),
        )
        .where(
            AgentBusinessReview.project_id == project,
            AgentBusinessReview.reviewed_by == actor,
            AgentBusinessReview.agent_id == agent,
            AgentBusinessReview.report_id.in_([row.id for row in tests]),
            AgentBusinessReview.deleted_at.is_(None),
        )
        .subquery()
    )
    reviews = list(
        (
            await db.scalars(
                select(AgentBusinessReview)
                .join(
                    latest_reviews,
                    latest_reviews.c.id == AgentBusinessReview.id,
                )
                .where(latest_reviews.c.rank == 1)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    report_map = {(row.connection_id, row.operation): row for row in tests}
    review_map = {row.report_id: row for row in reviews}
    evidence: list[JsonValue] = []
    for binding in bindings:
        for operation in sorted(binding.operations):
            report = report_map.get((binding.connection_id, operation))
            review = review_map.get(report.id) if report else None
            evidence.append(
                {
                    "binding": str(binding.id),
                    "connection": str(binding.connection_id),
                    "operation": operation,
                    "binding_revision": binding.revision,
                    "configuration_revision": binding.configuration_revision,
                    "authorization_revision": binding.authorization_revision,
                    "resource_revision": binding.resource_revision,
                    "report": None
                    if report is None
                    else {
                        "id": str(report.id),
                        "status": report.status,
                        "fingerprint": report.configuration_fingerprint,
                        "binding": str(report.binding_id),
                        "binding_revision": report.binding_revision,
                        (
                            'configuration_revision'
                        ): report.configuration_revision,
                        (
                            'authorization_revision'
                        ): report.authorization_revision,
                        "resource_revision": report.resource_revision,
                        "configuration_test": str(
                            report.configuration_test_id
                        ),
                        "configuration_test_until": _time(
                            report.configuration_test_valid_until
                        ),
                        "completed_at": _time(report.completed_at),
                        "receipt": report.receipt_json,
                        "error_code": report.error_code,
                    },
                    "review": None
                    if review is None
                    else {
                        "id": str(review.id),
                        "revision": review.revision,
                        "decision": review.decision,
                        "report_digest": review.report_digest,
                        "fingerprint": review.configuration_fingerprint,
                        "valid_until": _time(review.valid_until),
                    },
                }
            )
    # Only the opaque HMAC leaves this service. Customer/query data is never
    # returned.
    return private_digest(
        {
            "project": str(project),
            "actor": str(actor),
            "agent": str(agent),
            "evidence": evidence,
        }
    )
