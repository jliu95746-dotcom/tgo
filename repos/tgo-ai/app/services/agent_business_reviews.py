"""Append-only, optimistic and retry-safe judgments over owned business reports."""

from datetime import UTC, datetime
import asyncio
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.exceptions import AuthorizationError, ConflictError, NotFoundError
from app.models.agent_business_review import AgentBusinessReview
from app.models.agent_business_trial import AgentBusinessTrial
from app.schemas.agent_business_reviews import (
    BusinessReviewRequest,
    BusinessReviewRecord,
    BusinessReviewState,
)
from app.services.agent_activation import lock_account_agents
from app.services.agent_business_review_evidence import (
    BusinessReviewEvidence,
    CurrentContext,
    report_digest,
    validity,
)
from app.services.agent_business_trials import AgentBusinessTrialService
from app.services.agent_business_review_requests import (
    AgentBusinessReviewRequests,
)


class AgentBusinessReviewService:
    def __init__(self, db: AsyncSession, trials: AgentBusinessTrialService):
        self.db, self.trials = db, trials
        self.evidence = BusinessReviewEvidence(trials)

    async def _request(
        self, project: UUID, actor: UUID, request: UUID
    ) -> AgentBusinessReview | None:
        result: AgentBusinessReview | None = await self.db.scalar(
            select(AgentBusinessReview)
            .where(
                AgentBusinessReview.project_id == project,
                AgentBusinessReview.reviewed_by == actor,
                AgentBusinessReview.request_id == request,
                AgentBusinessReview.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        return result

    async def _records(
        self, project: UUID, actor: UUID, agent: UUID, report: UUID
    ) -> list[BusinessReviewRecord]:
        rows = await self.db.scalars(
            select(AgentBusinessReview)
            .where(
                AgentBusinessReview.project_id == project,
                AgentBusinessReview.reviewed_by == actor,
                AgentBusinessReview.agent_id == agent,
                AgentBusinessReview.report_id == report,
                AgentBusinessReview.deleted_at.is_(None),
            )
            .order_by(AgentBusinessReview.revision.desc())
            .limit(50)
            .execution_options(populate_existing=True)
        )
        return [
            BusinessReviewRecord.model_validate_json(row.result_json)
            for row in rows
        ]

    async def state(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        report_id: UUID,
        *,
        context: CurrentContext | None = None,
    ) -> BusinessReviewState:
        report = await self.trials.get(project, actor, agent, report_id)
        evidence = await self.evidence.assess(report, context=context)
        records = await self._records(project, actor, agent, report_id)
        latest = records[0] if records else None
        status, issues = evidence.status, list(evidence.issues)
        if (
            status == "current"
            and latest
            and (
                latest.report_digest != report_digest(report)
                or latest.valid_until <= datetime.now(UTC)
            )
        ):
            status = "changed"
            issues.append(
                "report_changed"
                if latest.report_digest != report_digest(report)
                else "report_expired"
            )
        await self.trials._actor(project, actor)
        if status == "current" and context is None:
            try:
                if (
                    await self.trials.fingerprint(
                        self.db, project, actor, agent
                    )
                    != report.configuration_fingerprint
                ):
                    status = "changed"
                    issues.append("configuration_changed")
            except AuthorizationError:
                raise
            except Exception:
                status = "unavailable"
                issues.append("evidence_unavailable")
        # Recheck the latest report after external owner calls. A newly queued
        # test supersedes the old one even before that query completes.
        if (
            status == "current"
            and await self.evidence.latest_report(report) != report.id
        ):
            status = "changed"
            issues.append("superseded")
        await self.trials._actor(project, actor)
        return BusinessReviewState(
            project_id=project,
            agent_id=agent,
            report_id=report_id,
            checked_by=actor,
            checked_at=datetime.now(UTC),
            status=status,
            review_status="unavailable"
            if status == "unavailable"
            else "needs_retest"
            if status != "current"
            else latest.decision
            if latest
            else "not_reviewed",
            issues=issues,
            can_review=status == "current",
            valid_until=evidence.valid_until,
            latest_review=latest,
            reviews=records,
        )

    async def request_result(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        report: UUID,
        request: UUID,
    ) -> BusinessReviewRecord:
        await self.trials.get(project, actor, agent, report)
        row = await self._request(project, actor, request)
        if row is None or row.agent_id != agent or row.report_id != report:
            raise NotFoundError("原业务评审请求")
        return BusinessReviewRecord.model_validate_json(row.result_json)

    async def review(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        report_id: UUID,
        command: BusinessReviewRequest,
    ) -> BusinessReviewRecord:
        return await AgentBusinessReviewRequests(self).submit(
            project, actor, agent, report_id, command
        )

    async def _review_record(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        report_id: UUID,
        command: BusinessReviewRequest,
        *,
        entry_id: UUID,
    ) -> BusinessReviewRecord:
        requests = AgentBusinessReviewRequests(self)
        digest = requests.digest(agent, report_id, command)
        try:
            await self.trials._actor(project, actor)
            await self.trials._agent(project, agent)
            report = await self.trials.get(project, actor, agent, report_id)
            async with asyncio.timeout(5):
                await lock_account_agents(self.db, project)
            previous = await self._request(project, actor, command.request_id)
            if previous:
                if (
                    previous.agent_id != agent
                    or previous.report_id != report_id
                    or previous.request_digest != digest
                ):
                    raise ConflictError("原业务评审请求内容不一致")
                result = BusinessReviewRecord.model_validate_json(
                    previous.result_json
                )
                await requests.install(entry_id, digest, result)
                await self.db.commit()
                return result
            if report.status == "running":
                raise ConflictError("业务测试尚未完成，不能评审")
            records = await self._records(project, actor, agent, report_id)
            latest = records[0] if records else None
            if (
                latest.id if latest else None
            ) != command.expected_latest_review_id:
                raise ConflictError("业务评审已有新记录，请刷新后确认")
            if command.decision == "passed":
                if not command.checks.confirmed():
                    raise ConflictError("请完成业务结果和只读范围核对")
                state = await self.state(project, actor, agent, report_id)
                if not state.can_review:
                    raise ConflictError("原业务测试依据已失效或不满足评审条件，请重测")
            # The account lock serializes configuration and reviews. Re-read the
            # owned immutable report before installing the new judgment.
            row = await self.db.scalar(
                select(AgentBusinessTrial)
                .where(
                    AgentBusinessTrial.id == report_id,
                    AgentBusinessTrial.project_id == project,
                    AgentBusinessTrial.actor_id == actor,
                    AgentBusinessTrial.agent_id == agent,
                    AgentBusinessTrial.deleted_at.is_(None),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None or self.trials.view(row) != report:
                raise ConflictError("原业务报告已经变化，请重新读取")
            now = datetime.now(UTC)
            result = BusinessReviewRecord.model_validate(
                dict(
                    id=uuid4(),
                    project_id=project,
                    agent_id=agent,
                    report_id=report_id,
                    connection_id=report.connection_id,
                    operation=report.operation,
                    reviewed_by=actor,
                    request_id=command.request_id,
                    revision=latest.revision + 1 if latest else 1,
                    report_digest=report_digest(report),
                    configuration_fingerprint=report.configuration_fingerprint,
                    decision=command.decision,
                    checks=command.checks,
                    reason=command.reason,
                    created_at=now,
                    valid_until=validity(report),
                )
            )
            self.db.add(
                AgentBusinessReview(
                    id=result.id,
                    project_id=project,
                    reviewed_by=actor,
                    agent_id=agent,
                    report_id=report_id,
                    connection_id=report.connection_id,
                    operation=report.operation,
                    request_id=command.request_id,
                    revision=result.revision,
                    request_digest=digest,
                    report_digest=result.report_digest,
                    configuration_fingerprint=report.configuration_fingerprint,
                    decision=command.decision,
                    result_json=result.model_dump_json(),
                    valid_until=result.valid_until,
                    created_at=now,
                )
            )
            await self.trials._actor(project, actor)
            await requests.install(entry_id, digest, result)
            await self.db.commit()
            return result
        except IntegrityError:
            await self.db.rollback()
            previous = await self._request(project, actor, command.request_id)
            if (
                previous
                and previous.agent_id == agent
                and previous.report_id == report_id
                and previous.request_digest == digest
            ):
                result = await self.request_result(
                    project, actor, agent, report_id, command.request_id
                )
                await requests.install(entry_id, digest, result)
                await self.db.commit()
                return result
            raise ConflictError("业务评审存在并发冲突，请读取原请求或刷新") from None
        except Exception:
            await self.db.rollback()
            raise
