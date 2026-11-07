"""Human judgments remain scoped, immutable, current and retry-safe."""
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from typing import Literal, Protocol, cast

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_test_report import AgentTestReport
from app.config import settings
from app.models.agent_trial_review import AgentTrialReview
from app.schemas.agent_trials import AgentTrialReport, TrialApplicability
from app.schemas.agent_trial_reviews import (
    KnowledgeReviewCase,
    KnowledgeReviewChecks,
    KnowledgeReviewCommand,
    KnowledgeReviewResponse,
    KnowledgeReviewState,
)
from app.services.agent_trial_fingerprint import private_digest
from app.services.agent_trial_dependencies import AgentTrialDependencies
from app.services.sensitive_data_redactor import SensitiveDataRedactor


def case_digest(case: KnowledgeReviewCase) -> str:
    definition = case.model_dump(mode="json")
    if case.valid_from is None:
        definition.pop("valid_from")  # Preserve pre-catalog case digests.
    return hashlib.sha256(
        json.dumps(
            definition,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def artifact_digest(report: AgentTrialReport) -> str:
    return private_digest(
        report.model_dump(
            mode="json", exclude={"applicability", "assessment", "can_enable"}
        )
    )


def valid_until(report: AgentTrialReport) -> datetime:
    observed = report.completed_at or report.created_at
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return observed + timedelta(
        hours=settings.agent_trial_review_validity_hours
    )


def _within_validity(report: AgentTrialReport) -> bool:
    return datetime.now(UTC) < valid_until(report)


def _eligible(report: AgentTrialReport, case: KnowledgeReviewCase) -> bool:
    if case.valid_from is not None:
        created = (
            report.created_at.replace(tzinfo=UTC)
            if report.created_at.tzinfo is None
            else report.created_at
        )
        if case.valid_from.tzinfo is None or created < case.valid_from:
            return False
    if (
        report.question.strip() != case.question.strip()
        or not report.answer.strip()
        or not _within_validity(report)
    ):
        return False
    if case.expected_result == "grounded_answer":
        return (
            report.status == "completed"
            and report.knowledge_status == "matched"
            and bool(report.sources)
        )
    if not any(marker in report.answer for marker in ["无法确认", "不能确认", "资料不足"]):
        return False
    return (
        report.status == "completed"
        and report.knowledge_status == "matched"
        and bool(report.sources)
    ) or (
        report.status == "blocked"
        and report.knowledge_status == "no_match"
        and report.error_code == "knowledge_not_verified"
    )


class TrialReviewReader(Protocol):
    dependencies: AgentTrialDependencies

    async def get(
        self, project_id: UUID, creator: UUID, agent_id: UUID, report_id: UUID
    ) -> AgentTrialReport:
        ...


class AgentTrialReviewService:
    def __init__(self, db: AsyncSession, trials: TrialReviewReader) -> None:
        self.db, self.trials = db, trials

    async def _applicability(
        self, report: AgentTrialReport, project: UUID
    ) -> TrialApplicability:
        if (
            report.status == "blocked"
            and report.knowledge_status == "no_match"
        ):
            return await self.trials.dependencies.check(
                report, project, allow_safe_refusal=True
            )
        return report.applicability

    async def _existing(
        self, project: UUID, reviewer: UUID, key: UUID
    ) -> AgentTrialReview | None:
        return cast(
            AgentTrialReview | None,
            await self.db.scalar(
                select(AgentTrialReview).where(
                    AgentTrialReview.project_id == project,
                    AgentTrialReview.reviewed_by == reviewer,
                    AgentTrialReview.request_key == key,
                    AgentTrialReview.deleted_at.is_(None),
                )
            ),
        )

    def _response(
        self,
        row: AgentTrialReview,
        report: AgentTrialReport,
        applicability: TrialApplicability,
        catalog: dict[str, str],
        superseded: bool,
    ) -> KnowledgeReviewResponse:
        stored_case = KnowledgeReviewCase.model_validate(row.case_spec)
        case_current = catalog.get(row.case_id) == row.case_digest
        expiry = (
            row.valid_until.replace(tzinfo=UTC)
            if row.valid_until.tzinfo is None
            else row.valid_until
        )
        expiry = min(expiry, valid_until(report))
        return KnowledgeReviewResponse(
            id=row.id,
            revision=row.revision,
            agent_id=row.agent_id,
            report_id=row.report_id,
            request_key=row.request_key,
            reviewed_by=row.reviewed_by,
            decision=cast(Literal["passed", "rejected"], row.decision),
            checks=KnowledgeReviewChecks.model_validate(row.checks),
            note=row.note,
            case=stored_case,
            case_status="current" if case_current else "changed",
            applicability=applicability,
            superseded=superseded,
            effective=row.decision == "passed"
            and not superseded
            and case_current
            and applicability.status == "current"
            and datetime.now(UTC) < expiry
            and row.report_digest == artifact_digest(report)
            and _eligible(report, stored_case),
            created_at=row.created_at,
            valid_until=expiry,
        )

    async def state(
        self,
        project: UUID,
        reviewer: UUID,
        agent: UUID,
        report_id: UUID,
        cases: list[KnowledgeReviewCase],
    ) -> KnowledgeReviewState:
        report = await self.trials.get(project, reviewer, agent, report_id)
        applicability = await self._applicability(report, project)
        available = [
            case
            for case in cases
            if case.question.strip() == report.question.strip()
        ]
        rows = (
            await self.db.scalars(
                select(AgentTrialReview)
                .where(
                    AgentTrialReview.project_id == project,
                    AgentTrialReview.reviewed_by == reviewer,
                    AgentTrialReview.agent_id == agent,
                    AgentTrialReview.report_id == report_id,
                    AgentTrialReview.deleted_at.is_(None),
                )
                .order_by(
                    AgentTrialReview.revision.desc(),
                    AgentTrialReview.created_at.desc(),
                    AgentTrialReview.id.desc(),
                )
                .limit(100)
            )
        ).all()
        seen: set[str] = set()
        results: list[KnowledgeReviewResponse] = []
        catalog = {case.id: case_digest(case) for case in cases}
        for row in rows:
            superseded = row.case_id in seen
            seen.add(row.case_id)
            results.append(
                self._response(row, report, applicability, catalog, superseded)
            )
        return KnowledgeReviewState(
            agent_id=agent,
            report_id=report_id,
            available_cases=available,
            applicability=applicability,
            can_review=applicability.status == "current"
            and any(_eligible(report, case) for case in available),
            reviews=results,
            valid_until=valid_until(report),
        )

    async def review(
        self,
        project: UUID,
        reviewer: UUID,
        agent: UUID,
        report_id: UUID,
        command: KnowledgeReviewCommand,
    ) -> KnowledgeReviewResponse:
        # Catalog changes do not change the original request identity.
        original = command.model_dump(mode="json", exclude={"current_cases"})
        if command.case.valid_from is None:
            original["case"].pop("valid_from")
        digest = private_digest(
            {"agent_id": str(agent), "report_id": str(report_id), **original}
        )
        cases = (
            command.current_cases
            if command.current_cases is not None
            else [command.case]
        )
        existing = await self._existing(project, reviewer, command.request_key)
        if existing:
            if existing.request_digest != digest:
                raise HTTPException(409, "该请求键已用于另一份评审，请刷新记录")
            return await self._receipt(
                project, reviewer, agent, report_id, cases, existing.id
            )
        if command.case not in cases:
            raise HTTPException(409, "测试问题版本已变化，请使用当前问题重新试聊")
        report = await self.trials.get(project, reviewer, agent, report_id)
        if (
            command.case_id != command.case.id
            or report.question.strip() != command.case.question.strip()
        ):
            raise HTTPException(409, "测试问题与岗位用例不一致，请按模板重新试聊")
        applicability = await self._applicability(report, project)
        if (
            report.knowledge_snapshot is None
            or report.configuration_fingerprint is None
            or report.status == "running"
        ):
            raise HTTPException(409, "报告尚未完成或缺少版本记录，请重新试聊")
        if command.decision == "passed" and (
            not command.checks.confirmed()
            or applicability.status != "current"
            or not _eligible(report, command.case)
        ):
            raise HTTPException(409, "本次报告不满足评审条件，请核对资料、完成确认或重新试聊")
        # Serialize judgments on the owned immutable report, including
        # competing tabs.
        owned = await self.db.scalar(
            select(AgentTestReport.id)
            .where(
                AgentTestReport.id == report_id,
                AgentTestReport.project_id == project,
                AgentTestReport.created_by == reviewer,
                AgentTestReport.agent_id == agent,
                AgentTestReport.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if owned is None:
            raise HTTPException(404, "未找到试聊报告")
        concurrent = await self._existing(
            project, reviewer, command.request_key
        )
        if concurrent:
            concurrent_digest, concurrent_id = (
                concurrent.request_digest,
                concurrent.id,
            )
            await self.db.rollback()
            if concurrent_digest != digest:
                raise HTTPException(409, "该请求键已用于另一份评审，请刷新记录")
            return await self._receipt(
                project, reviewer, agent, report_id, cases, concurrent_id
            )
        latest = await self.db.scalar(
            select(AgentTrialReview)
            .where(
                AgentTrialReview.project_id == project,
                AgentTrialReview.reviewed_by == reviewer,
                AgentTrialReview.report_id == report_id,
                AgentTrialReview.case_id == command.case_id,
                AgentTrialReview.deleted_at.is_(None),
            )
            .order_by(AgentTrialReview.revision.desc())
            .limit(1)
        )
        if (
            latest.id if latest else None
        ) != command.expected_latest_review_id:
            await self.db.rollback()
            raise HTTPException(409, "评审记录已经变化，请刷新后重新确认")
        row = AgentTrialReview(
            id=uuid4(),
            project_id=project,
            reviewed_by=reviewer,
            agent_id=agent,
            report_id=report_id,
            request_key=command.request_key,
            request_digest=digest,
            case_id=command.case_id,
            revision=latest.revision + 1 if latest else 1,
            case_digest=case_digest(command.case),
            case_spec=command.case.model_dump(mode="json"),
            report_digest=artifact_digest(report),
            configuration_fingerprint=report.configuration_fingerprint,
            knowledge_snapshot=report.knowledge_snapshot.model_dump(
                mode="json"
            ),
            decision=command.decision,
            checks=command.checks.model_dump(mode="json"),
            note=SensitiveDataRedactor().redact(command.note).text,
            valid_until=valid_until(report),
        )
        identifier = row.id
        self.db.add(row)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            retry = await self._existing(
                project, reviewer, command.request_key
            )
            if retry is None or retry.request_digest != digest:
                raise HTTPException(409, "评审存在冲突，请查询原记录") from None
            identifier = retry.id
        return await self._receipt(
            project, reviewer, agent, report_id, cases, identifier
        )

    async def _receipt(
        self,
        project: UUID,
        reviewer: UUID,
        agent: UUID,
        report_id: UUID,
        cases: list[KnowledgeReviewCase],
        identifier: UUID,
    ) -> KnowledgeReviewResponse:
        report = await self.trials.get(project, reviewer, agent, report_id)
        row = await self.db.scalar(
            select(AgentTrialReview).where(
                AgentTrialReview.id == identifier,
                AgentTrialReview.project_id == project,
                AgentTrialReview.reviewed_by == reviewer,
                AgentTrialReview.agent_id == agent,
                AgentTrialReview.report_id == report_id,
                AgentTrialReview.deleted_at.is_(None),
            )
        )
        if row is None:
            raise HTTPException(404, "未找到原评审")
        latest = await self.db.scalar(
            select(func.max(AgentTrialReview.revision)).where(
                AgentTrialReview.project_id == project,
                AgentTrialReview.reviewed_by == reviewer,
                AgentTrialReview.report_id == report_id,
                AgentTrialReview.case_id == row.case_id,
                AgentTrialReview.deleted_at.is_(None),
            )
        )
        return self._response(
            row,
            report,
            await self._applicability(report, project),
            {case.id: case_digest(case) for case in cases},
            row.revision != latest,
        )
