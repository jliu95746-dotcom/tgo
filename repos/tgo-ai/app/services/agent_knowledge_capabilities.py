"""Aggregate every current required case without falling back to old success."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.models.agent_test_report import AgentTestReport
from app.models.agent_trial_review import AgentTrialReview
from app.models.agent import Agent
from app.schemas.agent_knowledge_capabilities import (
    AgentKnowledgeCapabilities,
    KnowledgeCapabilityCoverage,
    KnowledgeCapabilityQuery,
    KnowledgeCaseCoverage,
    KnowledgeCaseStatus,
)
from app.schemas.agent_trials import AgentTrialReport, TrialKnowledgeSnapshot
from app.schemas.agent_trial_reviews import KnowledgeReviewCase
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_trial_reviews import (
    AgentTrialReviewService,
    case_digest,
    valid_until,
)


class AgentKnowledgeCapabilityService:
    def __init__(self, reviews: AgentTrialReviewService) -> None:
        self.reviews, self.db, self.trials = (
            reviews,
            reviews.db,
            reviews.trials,
        )

    async def read(
        self,
        project: UUID,
        reviewer: UUID,
        agent: UUID,
        query: KnowledgeCapabilityQuery,
    ) -> AgentKnowledgeCapabilities:
        enabled = await self.db.scalar(
            select(Agent.is_active).where(
                Agent.id == agent,
                Agent.project_id == project,
                Agent.deleted_at.is_(None),
            )
        )
        if enabled is None:
            raise HTTPException(404, "未找到 AI 员工")
        cache: dict[
            KnowledgeChannel, tuple[str, TrialKnowledgeSnapshot] | None
        ] = {}
        knowledge: Literal["available", "missing", "unknown"] = "unknown"
        try:
            current = await self.trials.dependencies.current(
                project,
                agent,
                query.channel,
                requested_by=reviewer,
            )
            cache[query.channel] = current
            snapshot = current[1]
            if (
                not snapshot.issues
                and snapshot.collections
                and all(row.resource_revision for row in snapshot.collections)
            ):
                knowledge = "available"
            elif snapshot.issues and set(snapshot.issues).issubset(
                {"no_binding"}
            ):
                knowledge = "missing"
        except Exception:
            cache[query.channel] = None

        groups: dict[str, list[KnowledgeCaseCoverage]] = {}
        for case in query.cases:
            row = await self._case(
                project, reviewer, agent, case, query.channel, cache
            )
            groups.setdefault(case.template_id, []).append(row)
        capabilities = []
        for rows in groups.values():
            answers = [
                row
                for row in rows
                if row.case.expected_result == "grounded_answer"
            ]
            safety = [
                row
                for row in rows
                if row.case.expected_result == "safe_refusal"
            ]
            # A safety-only catalog cannot grant answer coverage.
            status = self._status(
                rows, bool(answers) and bool(safety), knowledge
            )
            capabilities.append(
                KnowledgeCapabilityCoverage(
                    template_id=rows[0].case.template_id,
                    template_version=rows[0].case.template_version,
                    status=status,
                    answer_cases_passed=sum(
                        row.status == "passed" for row in answers
                    ),
                    answer_cases_required=len(answers),
                    safety_cases_passed=sum(
                        row.status == "passed" for row in safety
                    ),
                    safety_cases_required=len(safety),
                    cases=rows,
                    valid_until=min(
                        row.valid_until for row in rows if row.valid_until
                    )
                    if status == "passed"
                    else None,
                )
            )
        return AgentKnowledgeCapabilities(
            agent_id=agent,
            reviewed_by=reviewer,
            channel=query.channel,
            enabled=bool(enabled),
            knowledge_status=knowledge,
            checked_at=datetime.now(UTC),
            capabilities=capabilities,
        )

    @staticmethod
    def _status(
        rows: list[KnowledgeCaseCoverage],
        complete_catalog: bool,
        knowledge: Literal["available", "missing", "unknown"],
    ) -> KnowledgeCaseStatus:
        if knowledge != "available":
            return "unavailable"
        if complete_catalog and all(row.status == "passed" for row in rows):
            return "passed"
        states: tuple[KnowledgeCaseStatus, ...] = (
            "needs_retest",
            "failed",
            "in_progress",
            "awaiting_review",
            "not_tested",
            "unavailable",
        )
        for status in states:
            if any(row.status == status for row in rows):
                return status
        return "not_tested"

    async def _case(
        self,
        project: UUID,
        reviewer: UUID,
        agent: UUID,
        case: KnowledgeReviewCase,
        channel: KnowledgeChannel,
        cache: dict[
            KnowledgeChannel, tuple[str, TrialKnowledgeSnapshot] | None
        ],
    ) -> KnowledgeCaseCoverage:
        rows = (
            await self.db.scalars(
                select(AgentTestReport)
                .where(
                    AgentTestReport.project_id == project,
                    AgentTestReport.created_by == reviewer,
                    AgentTestReport.agent_id == agent,
                    AgentTestReport.channel == channel.value,
                    AgentTestReport.question == case.question.strip(),
                    AgentTestReport.deleted_at.is_(None),
                )
                .order_by(
                    AgentTestReport.created_at.desc(), AgentTestReport.id
                )
                .limit(2)
            )
        ).all()
        if not rows:
            return KnowledgeCaseCoverage(case=case, status="not_tested")
        # Identical timestamps cannot prove which independent test came last.
        if len(rows) == 2 and rows[0].created_at == rows[1].created_at:
            return KnowledgeCaseCoverage(case=case, status="needs_retest")
        report = AgentTrialReport.model_validate(rows[0])
        coverage = KnowledgeCaseCoverage(
            case=case, status="needs_retest", report_id=report.id
        )
        created = (
            report.created_at.replace(tzinfo=UTC)
            if report.created_at.tzinfo is None
            else report.created_at
        )
        if case.valid_from is not None and (
            case.valid_from.tzinfo is None or created < case.valid_from
        ):
            return coverage
        if report.status == "running":
            expiry = rows[0].expires_at
            expiry = (
                expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry
            )
            coverage.status = (
                "in_progress" if expiry > datetime.now(UTC) else "needs_retest"
            )
            return coverage
        if report.status in {"failed", "cancelled"}:
            coverage.status = "failed"
            return coverage
        applicable = await self.trials.dependencies.check(
            report, project, cache, allow_safe_refusal=True
        )
        if applicable.status != "current" or valid_until(
            report
        ) <= datetime.now(UTC):
            return coverage
        review = await self.db.scalar(
            select(AgentTrialReview)
            .where(
                AgentTrialReview.project_id == project,
                AgentTrialReview.reviewed_by == reviewer,
                AgentTrialReview.agent_id == agent,
                AgentTrialReview.report_id == report.id,
                AgentTrialReview.case_id == case.id,
                AgentTrialReview.deleted_at.is_(None),
            )
            .order_by(AgentTrialReview.revision.desc())
            .limit(1)
        )
        if review is None:
            coverage.status = "awaiting_review"
            return coverage
        receipt = self.reviews._response(
            review, report, applicable, {case.id: case_digest(case)}, False
        )
        coverage.review_id, coverage.review_revision = (
            receipt.id,
            receipt.revision,
        )
        coverage.valid_until = receipt.valid_until
        if receipt.effective:
            coverage.status = "passed"
        elif (
            receipt.case_status == "current" and receipt.decision == "rejected"
        ):
            coverage.status = "failed"
        return coverage
