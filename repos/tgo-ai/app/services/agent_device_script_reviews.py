"""Review a fixed result and retain the original decision on revocation."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from app.exceptions import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
)
from app.models.agent_device_script_review import (
    AgentDeviceScriptReview,
)
from app.models.agent_device_script_trial import AgentDeviceScriptTrial
from app.schemas.agent_device_script_reviews import (
    ReviewStatus,
    RunCheckStatus,
    ScriptReviewProof,
    ScriptReviewRecord,
    ScriptReviewState,
    ScriptReviewWrite,
    ScriptRunCheck,
)
from app.services.agent_activation import lock_account_agents
from app.services.agent_device_script_cases import (
    AgentDeviceScriptCaseService,
)
from app.services.agent_device_script_evidence import (
    read_trial_evidence,
)
from app.services.agent_trial_fingerprint import private_digest


class AgentDeviceScriptReviews:
    def __init__(self, cases: AgentDeviceScriptCaseService) -> None:
        self.cases, self.db = cases, cases.db

    async def _review(
        self, trial: AgentDeviceScriptTrial
    ) -> AgentDeviceScriptReview | None:
        row: AgentDeviceScriptReview | None = await self.db.scalar(
            select(AgentDeviceScriptReview)
            .where(AgentDeviceScriptReview.report_id == trial.id)
            .execution_options(populate_existing=True)
        )
        if row is not None and (
            row.project_id != trial.project_id
            or row.actor_id != trial.actor_id
            or row.agent_id != trial.agent_id
            or row.deleted_at is not None
        ):
            raise ConflictError("原评审记录范围无法确认")
        return row

    async def _proof(
        self, trial: AgentDeviceScriptTrial
    ) -> tuple[ScriptReviewProof, datetime]:
        evidence = await read_trial_evidence(self.cases, trial)
        case = evidence.case
        if (
            trial.status not in {"matched", "mismatched"}
            or trial.completed_at is None
            or trial.error_code is not None
            or trial.result_digest is None
            or evidence.status != "available"
            or case is None
            or case.status != "current"
            or evidence.values is None
            or evidence.expires_at is None
            or case.binding_id != trial.binding_id
            or case.binding_revision != trial.binding_revision
            or case.device_id != trial.device_id
            or case.policy_digest != trial.policy_digest
            or case.grant_digest != trial.grant_digest
            or private_digest(dict(evidence.values.expected_fields))
            != trial.expected_digest
            or (trial.status == "matched")
            != (trial.result_digest == trial.expected_digest)
        ):
            raise ConflictError("原试验或用例证据已变化，请重新测试")
        expires_at = min(
            trial.expires_at, case.expires_at, evidence.expires_at
        )
        if expires_at <= datetime.now(timezone.utc):
            raise ConflictError("原试验或用例证据已过期")
        return (
            ScriptReviewProof(
                case_version_id=case.id,
                case_digest=case.case_digest,
                result_digest=trial.result_digest,
            ),
            expires_at,
        )

    async def _view(
        self,
        trial: AgentDeviceScriptTrial,
        row: AgentDeviceScriptReview | None,
    ) -> ScriptReviewState:
        status: ReviewStatus = "unreviewed"
        record = None
        if row is not None:
            record = ScriptReviewRecord(
                id=row.id,
                case_version_id=row.case_version_id,
                case_digest=row.case_digest,
                result_digest=row.result_digest,
                decision=row.decision,
                reviewed_at=row.created_at,
                expires_at=row.expires_at,
                revoked_at=row.revoked_at,
            )
            if row.revoked_at is not None:
                status = "revoked"
            elif record.decision == "rejected":
                status = "rejected"
            elif row.expires_at <= datetime.now(timezone.utc):
                status = "expired"
            else:
                try:
                    proof, _ = await self._proof(trial)
                    current = (
                        proof.case_version_id == row.case_version_id
                        and proof.case_digest == row.case_digest
                        and proof.result_digest == row.result_digest
                        and trial.status == "matched"
                    )
                    status = "approved" if current else "changed"
                except (ConflictError, NotFoundError):
                    status = "changed"
        return ScriptReviewState(
            project_id=trial.project_id,
            actor_id=trial.actor_id,
            agent_id=trial.agent_id,
            report_id=trial.id,
            status=status,
            review=record,
        )

    async def read(
        self, project: UUID, actor: UUID, agent: UUID, report: UUID
    ) -> ScriptReviewState:
        await self.cases._authorize(project, actor, agent)
        trial = await self.cases.trials._row(
            project, actor, agent, report=report
        )
        return await self._view(trial, await self._review(trial))

    async def decide(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        report: UUID,
        command: ScriptReviewWrite,
    ) -> ScriptReviewState:
        await self.cases._authorize(project, actor, agent)
        await lock_account_agents(self.db, project)
        trial = await self.cases.trials._row(
            project, actor, agent, report=report
        )
        row = await self._review(trial)
        if row is None:
            employee = await self.cases.trials.bindings._agent(
                project, agent
            )
            if employee.is_active:
                raise ConflictError("请先停用员工再提交脚本评审")
            proof, expires_at = await self._proof(trial)
            if (
                command.model_dump(exclude={"decision"})
                != proof.model_dump()
            ):
                raise ConflictError("评审证据已变化，请重新读取原执行结果")
            if (
                command.decision == "approved"
                and trial.status != "matched"
            ):
                raise ConflictError("实际结果与预期不一致，不能批准")
            now = datetime.now(timezone.utc)
            if expires_at <= now:
                raise ConflictError("评审证据已过期")
            row = AgentDeviceScriptReview(
                project_id=project,
                actor_id=actor,
                agent_id=agent,
                report_id=report,
                decision=command.decision,
                case_version_id=proof.case_version_id,
                case_digest=proof.case_digest,
                result_digest=proof.result_digest,
                created_at=now,
                expires_at=expires_at,
            )
            self.db.add(row)
            await self.db.flush()
        elif (
            row.decision != command.decision
            or row.case_version_id != command.case_version_id
            or row.case_digest != command.case_digest
            or row.result_digest != command.result_digest
        ):
            raise ConflictError("原评审已有结论，请重新测试后评审")
        state = await self._view(trial, row)
        await self.db.commit()
        return state

    async def check_run(
        self, project: UUID, actor: UUID, agent: UUID, report: UUID
    ) -> ScriptRunCheck:
        state = await self.read(project, actor, agent, report)
        status: RunCheckStatus = "review_required"
        budget = None
        if state.status == "approved":
            trial = await self.cases.trials._row(
                project, actor, agent, report=report
            )
            try:
                budget = await self.cases.trials._current(trial)
                status = (
                    "ready"
                    if budget.remaining_runs
                    else "budget_exhausted"
                )
            except (ConflictError, NotFoundError):
                status = "device_changed"
            except (ExternalServiceError, ValueError, ValidationError):
                status = "unavailable"
            # Writers share this lock. Re-read after network I/O.
            await lock_account_agents(self.db, project)
            trial = await self.cases.trials._row(
                project, actor, agent, report=report
            )
            current = await self._view(trial, await self._review(trial))
            if current.status != "approved":
                status = "review_required"
                budget = None
            elif current.review != state.review:
                status = "device_changed"
                budget = None
            state = current
        now = datetime.now(timezone.utc)
        valid_until = None
        if (
            status == "ready"
            and budget is not None
            and state.review is not None
        ):
            valid_until = min(
                state.review.expires_at,
                budget.observed_at + timedelta(seconds=30),
                now + timedelta(seconds=30),
            )
            if valid_until <= now:
                status, budget, valid_until = (
                    "device_changed",
                    None,
                    None,
                )
        result = ScriptRunCheck(
            review=state,
            status=status,
            remaining_runs=budget.remaining_runs
            if budget is not None
            else None,
            checked_at=now,
            valid_until=valid_until,
        )
        await self.db.commit()
        return result

    async def revoke(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        report: UUID,
        review_id: UUID,
    ) -> ScriptReviewState:
        await self.cases._authorize(project, actor, agent)
        await lock_account_agents(self.db, project)
        trial = await self.cases.trials._row(
            project, actor, agent, report=report
        )
        row = await self._review(trial)
        if (
            row is None
            or row.id != review_id
            or row.decision != "approved"
        ):
            raise ConflictError("原批准记录无法确认")
        if row.revoked_at is None:
            row.revoked_at = max(
                datetime.now(timezone.utc), row.created_at
            )
        state = await self._view(trial, row)
        await self.db.commit()
        return state
