"""Current per-binding operation evidence; formal execution remains a separate gate."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID
from pydantic import TypeAdapter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AuthorizationError, ConflictError
from app.models.agent_business_connection import AgentBusinessConnection
from app.models.agent_business_trial import AgentBusinessTrial
from app.schemas.agent_business_reviews import (
    AgentBusinessCapabilities,
    BusinessOperationCapability,
)
from app.schemas.agent_business_trials import Operation
from app.services.agent_business_reviews import AgentBusinessReviewService
from app.services.agent_business_review_evidence import CurrentContext

_operations = TypeAdapter(list[Operation])


class AgentBusinessCapabilityService:
    def __init__(self, db: AsyncSession, reviews: AgentBusinessReviewService):
        self.db, self.reviews = db, reviews
        self.trials = reviews.trials

    async def _latest(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        connection: UUID,
        operation: Operation,
    ) -> AgentBusinessTrial | None:
        rows = await self.db.scalars(
            select(AgentBusinessTrial)
            .where(
                AgentBusinessTrial.project_id == project,
                AgentBusinessTrial.actor_id == actor,
                AgentBusinessTrial.agent_id == agent,
                AgentBusinessTrial.connection_id == connection,
                AgentBusinessTrial.operation == operation,
                AgentBusinessTrial.deleted_at.is_(None),
            )
            .order_by(
                AgentBusinessTrial.created_at.desc(), AgentBusinessTrial.id.desc()
            )
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return rows.first()

    async def _operation(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        binding: AgentBusinessConnection,
        operation: Operation,
        contexts: dict[UUID, CurrentContext],
    ) -> BusinessOperationCapability:
        result = BusinessOperationCapability(
            binding_id=binding.id,
            binding_revision=binding.revision,
            connection_id=binding.connection_id,
            operation=operation,
            status="not_tested",
            report_id=None,
            review_id=None,
            valid_until=None,
            issues=[],
        )
        row = await self._latest(
            project, actor, agent, binding.connection_id, operation
        )
        if row is None:
            return result
        report = await self.trials.get(project, actor, agent, row.id)
        result.report_id = report.id
        if report.status == "running":
            result.status = "testing"
            return result
        if (
            report.status != "completed"
            or report.receipt is None
            or report.receipt.outcome != "passed"
        ):
            result.status = "test_failed"
            result.issues = ["test_not_passed"]
            return result
        try:
            if binding.connection_id not in contexts:
                contexts[binding.connection_id] = await self.trials._current(
                    project,
                    actor,
                    agent,
                    binding.connection_id,
                    operation,
                    require_paused=False,
                )
            state = await self.reviews.state(
                project,
                actor,
                agent,
                report.id,
                context=contexts[binding.connection_id],
            )
        except AuthorizationError:
            raise
        except ConflictError:
            result.status = "needs_retest"
            result.issues = ["configuration_changed"]
            return result
        except Exception:
            result.status = "unavailable"
            result.issues = ["evidence_unavailable"]
            return result
        result.valid_until = state.valid_until
        result.review_id = state.latest_review.id if state.latest_review else None
        result.issues = list(state.issues)
        result.status = (
            "unavailable"
            if state.status == "unavailable"
            else "needs_retest"
            if state.status != "current"
            else (
                "passed"
                if state.review_status == "passed"
                else "rejected"
                if state.review_status == "rejected"
                else "review_required"
            )
        )
        return result

    async def read(
        self, project: UUID, actor: UUID, agent: UUID
    ) -> AgentBusinessCapabilities:
        await self.trials._actor(project, actor)
        await self.trials._agent(project, agent)
        bindings = list(
            (
                await self.db.scalars(
                    select(AgentBusinessConnection)
                    .where(
                        AgentBusinessConnection.project_id == project,
                        AgentBusinessConnection.agent_id == agent,
                        AgentBusinessConnection.enabled.is_(True),
                    )
                    .order_by(AgentBusinessConnection.connection_id)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        try:
            operations = {
                row.id: _operations.validate_python(
                    row.operations, strict=True,
                )
                for row in bindings
            }
            if any(
                len(values) != len(set(values))
                for values in operations.values()
            ):
                raise ValueError("Duplicate operation")
        except (TypeError, ValueError):
            raise ConflictError("业务连接操作配置无效，请重新保存关联") from None
        if sum(len(values) for values in operations.values()) > 200:
            raise ConflictError("当前员工关联过多，请整理业务连接后核验")
        values: list[BusinessOperationCapability] = []
        contexts: dict[UUID, CurrentContext] = {}
        try:
            async with asyncio.timeout(20):
                for binding in bindings:
                    for operation in operations[binding.id]:
                        values.append(
                            await self._operation(
                                project, actor, agent, binding, operation, contexts
                            )
                        )
        except TimeoutError:
            values = [
                BusinessOperationCapability(
                    binding_id=binding.id,
                    binding_revision=binding.revision,
                    connection_id=binding.connection_id,
                    operation=operation,
                    status="unavailable",
                    report_id=None,
                    review_id=None,
                    valid_until=None,
                    issues=["evidence_unavailable"],
                )
                for binding in bindings
                for operation in operations[binding.id]
            ]
        await self.trials._actor(project, actor)
        # Final local freshness fence: configuration changes, a later test or a
        # newer judgment cannot leave an earlier green operation in this read.
        final_fingerprint = None
        if any(value.status == "passed" for value in values):
            try:
                final_fingerprint = await self.trials.fingerprint(
                    self.db, project, actor, agent
                )
            except AuthorizationError:
                raise
            except Exception:
                for value in values:
                    if value.status == "passed":
                        value.status = "unavailable"
                        value.issues = ["evidence_unavailable"]
        for value in values:
            if value.status != "passed":
                continue
            if final_fingerprint != contexts[value.connection_id][2]:
                value.status = "needs_retest"
                value.issues = ["configuration_changed"]
                continue
            latest = await self._latest(
                project, actor, agent, value.connection_id, value.operation
            )
            current_binding = await self.db.get(
                AgentBusinessConnection, value.binding_id, populate_existing=True
            )
            records = (
                await self.reviews._records(project, actor, agent, value.report_id)
                if value.report_id
                else []
            )
            if (
                current_binding is None
                or not current_binding.enabled
                or current_binding.revision != value.binding_revision
                or value.operation not in current_binding.operations
                or latest is None
                or latest.id != value.report_id
                or not records
                or records[0].id != value.review_id
            ):
                value.status = "needs_retest"
                value.issues = ["superseded"]
            elif value.valid_until is None or value.valid_until <= datetime.now(UTC):
                value.status = "needs_retest"
                value.issues = ["report_expired"]
        await self.trials._actor(project, actor)
        return AgentBusinessCapabilities(
            project_id=project,
            agent_id=agent,
            checked_by=actor,
            checked_at=datetime.now(UTC),
            capabilities=values,
        )
