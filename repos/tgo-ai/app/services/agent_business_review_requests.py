"""Immutable review intentions, bounded retries, and original-key cancellation."""

import asyncio
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4
from sqlalchemy import func, select, update

from app.exceptions import ConflictError
from app.models.agent_business_review_request import AgentBusinessReviewRequest
from app.schemas.agent_business_review_requests import (
    BusinessReviewRequestState,
    ReviewRequestError,
)
from app.schemas.agent_business_reviews import (
    BusinessReviewRecord,
    BusinessReviewRequest,
)
from app.services.agent_trial_fingerprint import private_digest

if TYPE_CHECKING:
    from app.services.agent_business_reviews import AgentBusinessReviewService


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class AgentBusinessReviewRequests:
    def __init__(
        self,
        reviews: "AgentBusinessReviewService",
        *,
        review_seconds: float = 30,
        lock_seconds: float = 5,
    ):
        self.reviews, self.db = reviews, reviews.db
        self.review_seconds, self.lock_seconds = review_seconds, lock_seconds

    @staticmethod
    def digest(agent: UUID, report: UUID, command: BusinessReviewRequest) -> str:
        return private_digest(
            {
                "agent_id": str(agent),
                "report_id": str(report),
                "command": command.model_dump(mode="json"),
            }
        )

    async def _lock(self, project: UUID, actor: UUID, request: UUID) -> None:
        # A separate request lock lets cancellation proceed during account-wide
        # evidence checks. Final installation uses a conditional row update.
        if self.db.get_bind().dialect.name == "postgresql":
            key = int.from_bytes(
                hashlib.sha256(
                    f"business-review:{project}:{actor}:{request}".encode()
                ).digest()[:8],
                "big",
                signed=True,
            )
            async with asyncio.timeout(self.lock_seconds):
                await self.db.execute(select(func.pg_advisory_xact_lock(key)))

    async def _find(
        self, project: UUID, actor: UUID, request: UUID
    ) -> AgentBusinessReviewRequest | None:
        row: AgentBusinessReviewRequest | None = await self.db.scalar(
            select(AgentBusinessReviewRequest)
            .where(
                AgentBusinessReviewRequest.project_id == project,
                AgentBusinessReviewRequest.actor_id == actor,
                AgentBusinessReviewRequest.request_id == request,
            )
            .execution_options(populate_existing=True)
        )
        return row

    @staticmethod
    def _scope(row: AgentBusinessReviewRequest, agent: UUID, report: UUID) -> None:
        if row.agent_id != agent or row.report_id != report:
            raise ConflictError("原评审请求范围不一致")

    async def register(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        report: UUID,
        command: BusinessReviewRequest,
    ) -> AgentBusinessReviewRequest:
        digest = self.digest(agent, report, command)
        try:
            async with asyncio.timeout(self.lock_seconds * 2):
                await self.reviews.trials.get(project, actor, agent, report)
                await self._lock(project, actor, command.request_id)
                row = await self._find(project, actor, command.request_id)
                if row is not None:
                    self._scope(row, agent, report)
                    if row.command_digest is not None and not hmac.compare_digest(
                        row.command_digest, digest
                    ):
                        raise ConflictError(
                            "原评审命令已保存，不能更改其内容或版本依据"
                        )
                    if row.status == "completed" and row.command_digest is None:
                        previous = await self.reviews._request(
                            project, actor, command.request_id
                        )
                        if (
                            previous is None
                            or previous.agent_id != agent
                            or previous.report_id != report
                            or previous.request_digest != digest
                        ):
                            raise ConflictError("原业务评审请求内容不一致")
                    await self.db.commit()
                    return row
                previous = await self.reviews._request(
                    project, actor, command.request_id
                )
                if previous is not None and (
                    previous.agent_id != agent
                    or previous.report_id != report
                    or previous.request_digest != digest
                ):
                    raise ConflictError("原业务评审请求内容不一致")
                now = datetime.now(UTC)
                row = AgentBusinessReviewRequest(
                    id=uuid4(),
                    project_id=project,
                    actor_id=actor,
                    agent_id=agent,
                    report_id=report,
                    request_id=command.request_id,
                    command_digest=digest,
                    command_json=command.model_dump_json(),
                    status="completed" if previous else "pending",
                    created_at=now,
                    deadline_at=now + timedelta(seconds=self.review_seconds),
                    completed_at=now if previous else None,
                    result_json=previous.result_json if previous else None,
                )
                self.db.add(row)
                await self.db.commit()
                return row
        except BaseException:
            await self.db.rollback()
            raise

    async def _terminate(self, entry_id: UUID, error: ReviewRequestError) -> None:
        async with asyncio.timeout(self.lock_seconds):
            await self.db.execute(
                update(AgentBusinessReviewRequest)
                .where(
                    AgentBusinessReviewRequest.id == entry_id,
                    AgentBusinessReviewRequest.status == "pending",
                )
                .values(
                    status="failed", error_code=error, completed_at=datetime.now(UTC)
                )
                .execution_options(synchronize_session=False)
            )
            await self.db.commit()

    async def _view(
        self, row: AgentBusinessReviewRequest
    ) -> BusinessReviewRequestState:
        if row.status == "pending" and utc(row.deadline_at) <= datetime.now(UTC):
            await self._terminate(row.id, "review_timeout")
            await self.db.refresh(row)
        command = (
            BusinessReviewRequest.model_validate_json(row.command_json)
            if row.command_json
            else None
        )
        result = (
            BusinessReviewRecord.model_validate_json(row.result_json)
            if row.result_json
            else None
        )
        now = datetime.now(UTC)
        return BusinessReviewRequestState.model_validate(
            {
                **{
                    key: getattr(row, key)
                    for key in BusinessReviewRequestState.model_fields
                    if key
                    not in {
                        "scope",
                        "can_enable",
                        "checked_at",
                        "can_resume",
                        "processing",
                        "command",
                        "result",
                    }
                },
                "checked_at": now,
                "can_resume": row.status == "pending"
                and row.claimed_at is None
                and command is not None
                and utc(row.deadline_at) > now,
                "command": command,
                "result": result,
                "processing": row.status == "pending" and row.claimed_at is not None,
            }
        )

    async def state(
        self, project: UUID, actor: UUID, agent: UUID, report: UUID, request: UUID
    ) -> BusinessReviewRequestState:
        await self.reviews.trials.get(project, actor, agent, report)
        row = await self._find(project, actor, request)
        if row is not None:
            self._scope(row, agent, report)
            return await self._view(row)
        previous = await self.reviews.request_result(
            project, actor, agent, report, request
        )
        return BusinessReviewRequestState(
            project_id=project,
            actor_id=actor,
            agent_id=agent,
            report_id=report,
            request_id=request,
            status="completed",
            created_at=previous.created_at,
            deadline_at=previous.created_at + timedelta(seconds=self.review_seconds),
            completed_at=previous.created_at,
            checked_at=datetime.now(UTC),
            can_resume=False,
            processing=False,
            error_code=None,
            command=None,
            result=previous,
        )

    async def install(
        self, entry_id: UUID, digest: str, result: BusinessReviewRecord
    ) -> None:
        # This update and the actual review record commit in the SAME transaction.
        # A cancellation that wins the row cannot be overwritten by late approval.
        claimed: UUID | None = await self.db.scalar(
            update(AgentBusinessReviewRequest)
            .where(
                AgentBusinessReviewRequest.id == entry_id,
                AgentBusinessReviewRequest.project_id == result.project_id,
                AgentBusinessReviewRequest.actor_id == result.reviewed_by,
                AgentBusinessReviewRequest.agent_id == result.agent_id,
                AgentBusinessReviewRequest.report_id == result.report_id,
                AgentBusinessReviewRequest.request_id == result.request_id,
                AgentBusinessReviewRequest.command_digest == digest,
                AgentBusinessReviewRequest.status == "pending",
                AgentBusinessReviewRequest.deadline_at > datetime.now(UTC),
            )
            .values(
                status="completed",
                completed_at=datetime.now(UTC),
                result_json=result.model_dump_json(),
            )
            .returning(AgentBusinessReviewRequest.id)
            .execution_options(synchronize_session=False)
        )
        if claimed is not None:
            return
        row = await self._find(result.project_id, result.reviewed_by, result.request_id)
        if (
            row is not None
            and row.id == entry_id
            and row.status == "completed"
            and row.result_json
            and BusinessReviewRecord.model_validate_json(row.result_json) == result
        ):
            return
        raise ConflictError("原评审已取消、终止或到期，请读取原请求")

    async def submit(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        report: UUID,
        command: BusinessReviewRequest,
    ) -> BusinessReviewRecord:
        row = await self.register(project, actor, agent, report, command)
        state = await self._view(row)
        if state.result is not None:
            return state.result
        if not state.can_resume:
            raise ConflictError("原评审已取消或终止，请重新核对后创建新评审")
        entry_id = row.id
        claimed: UUID | None = await self.db.scalar(
            update(AgentBusinessReviewRequest)
            .where(
                AgentBusinessReviewRequest.id == entry_id,
                AgentBusinessReviewRequest.status == "pending",
                AgentBusinessReviewRequest.claimed_at.is_(None),
                AgentBusinessReviewRequest.deadline_at > datetime.now(UTC),
            )
            .values(claimed_at=datetime.now(UTC))
            .returning(AgentBusinessReviewRequest.id)
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()
        if claimed is None:
            raise ConflictError("原评审正在处理或已终止，请查询原请求")
        try:
            async with asyncio.timeout(
                max(0, (state.deadline_at - datetime.now(UTC)).total_seconds())
            ):
                return await self.reviews._review_record(
                    project, actor, agent, report, command, entry_id=entry_id
                )
        except BaseException as error:
            await self.db.rollback()
            category: ReviewRequestError = (
                "review_timeout"
                if isinstance(error, TimeoutError)
                else "interrupted"
                if isinstance(error, asyncio.CancelledError)
                else "review_failed"
            )
            await self._terminate(entry_id, category)
            if isinstance(error, TimeoutError):
                raise ConflictError("原评审处理已到期，请读取原请求结果") from None
            raise

    async def resume(
        self, project: UUID, actor: UUID, agent: UUID, report: UUID, request: UUID
    ) -> BusinessReviewRecord:
        state = await self.state(project, actor, agent, report, request)
        if state.result is not None:
            return state.result
        if not state.can_resume or state.command is None:
            raise ConflictError("原评审不能继续提交，请重新读取并核对")
        return await self.submit(project, actor, agent, report, state.command)

    async def cancel(
        self, project: UUID, actor: UUID, agent: UUID, report: UUID, request: UUID
    ) -> BusinessReviewRequestState:
        try:
            async with asyncio.timeout(self.lock_seconds * 2):
                await self.reviews.trials.get(project, actor, agent, report)
                await self._lock(project, actor, request)
                row = await self._find(project, actor, request)
                if row is None:
                    previous = await self.reviews._request(project, actor, request)
                    if previous is not None and (
                        previous.agent_id != agent or previous.report_id != report
                    ):
                        raise ConflictError("原评审请求范围不一致")
                    now = datetime.now(UTC)
                    row = AgentBusinessReviewRequest(
                        id=uuid4(),
                        project_id=project,
                        actor_id=actor,
                        agent_id=agent,
                        report_id=report,
                        request_id=request,
                        status="completed" if previous else "cancelled",
                        created_at=now,
                        deadline_at=now + timedelta(seconds=self.review_seconds),
                        completed_at=now,
                        error_code=None if previous else "cancelled",
                        result_json=previous.result_json if previous else None,
                    )
                    self.db.add(row)
                else:
                    self._scope(row, agent, report)
                    await self.db.execute(
                        update(AgentBusinessReviewRequest)
                        .where(
                            AgentBusinessReviewRequest.id == row.id,
                            AgentBusinessReviewRequest.status == "pending",
                        )
                        .values(
                            status="cancelled",
                            error_code="cancelled",
                            completed_at=datetime.now(UTC),
                        )
                        .execution_options(synchronize_session=False)
                    )
                await self.db.commit()
                await self.db.refresh(row)
                return await self._view(row)
        except BaseException:
            await self.db.rollback()
            raise
