"""Durable, idempotent employee trials; no automatic capability approval."""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from anyio import CancelScope
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.exceptions import (
    AuthorizationError, ExternalServiceError,
    ModelConfigurationVerificationError,
)

from app.models.agent_test_report import AgentTestReport
from app.runtime.supervisor.application.service import SupervisorRuntimeService
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from app.schemas.agent_trials import (
    AgentTrialReport, AgentTrialRequest, KnowledgeStatus, TrialKnowledgeSnapshot, TrialKnowledgeSource,
)
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_trial_dependencies import AgentTrialDependencies, compare_snapshots, read_knowledge_snapshot
from app.services.agent_service import AgentService
from app.services.agent_trial_fingerprint import configuration_fingerprint, private_digest
from app.services.sensitive_data_redactor import SensitiveDataRedactor

logger = get_logger(__name__)


class TrialOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "blocked", "failed", "cancelled"]
    answer: str = ""
    knowledge_status: KnowledgeStatus | None = None
    sources: list[TrialKnowledgeSource] = Field(default_factory=list)
    knowledge_snapshot: TrialKnowledgeSnapshot | None = None
    error_code: str | None = None
    error_message: str | None = None


class AgentTrialService:
    def __init__(self, db: AsyncSession, runtime: SupervisorRuntimeService) -> None:
        self.db = db
        self.runtime = runtime
        self.redactor = SensitiveDataRedactor()
        self.dependencies = AgentTrialDependencies(db)

    async def _report(
        self, row: AgentTestReport,
        cache: dict[KnowledgeChannel, tuple[str, TrialKnowledgeSnapshot] | None] | None = None,
    ) -> AgentTrialReport:
        report = AgentTrialReport.model_validate(row)
        return report.model_copy(update={"applicability": await self.dependencies.check(
            report, row.project_id, cache, requested_by=row.created_by,
        )})

    async def get(self, project_id: UUID, creator: UUID, agent_id: UUID, report_id: UUID) -> AgentTrialReport:
        row = await self.db.scalar(select(AgentTestReport).where(
            AgentTestReport.id == report_id, AgentTestReport.project_id == project_id,
            AgentTestReport.created_by == creator, AgentTestReport.agent_id == agent_id,
            AgentTestReport.deleted_at.is_(None),
        ))
        if row is None:
            raise HTTPException(404, "未找到试聊报告")
        return await self._report(await self._expire(row))

    async def list(self, project_id: UUID, creator: UUID, agent_id: UUID, limit: int = 20) -> list[AgentTrialReport]:
        rows = await self.db.scalars(select(AgentTestReport).where(
            AgentTestReport.project_id == project_id, AgentTestReport.created_by == creator,
            AgentTestReport.agent_id == agent_id, AgentTestReport.deleted_at.is_(None),
        ).order_by(AgentTestReport.created_at.desc(), AgentTestReport.id).limit(limit))
        cache: dict[KnowledgeChannel, tuple[str, TrialKnowledgeSnapshot] | None] = {}
        return [await self._report(await self._expire(row), cache) for row in rows]

    async def _expire(self, row: AgentTestReport) -> AgentTestReport:
        # The loaded entity may already be stale after another request finishes.
        await self.db.execute(update(AgentTestReport).where(
            AgentTestReport.id == row.id, AgentTestReport.status == "running",
            AgentTestReport.expires_at <= datetime.now(UTC),
        ).values(
            status="failed", error_code="interrupted", answer="", sources=[],
            error_message="试聊已中断，请使用新的测试请求重新测试",
            completed_at=datetime.now(UTC),
        ).execution_options(synchronize_session=False))
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def by_request(self, project_id: UUID, creator: UUID, agent_id: UUID, key: UUID) -> AgentTrialReport:
        row = await self._existing(project_id, creator, key)
        if row is None or row.agent_id != agent_id or row.deleted_at is not None:
            raise HTTPException(404, "未找到试聊报告")
        return await self._report(await self._expire(row))

    def _request_digest(self, agent_id: UUID, request: AgentTrialRequest) -> str:
        digest_input: dict[str, JsonValue] = {
            "agent_id": str(agent_id), "message": request.message,
            "channel": request.channel.value, "timeout": request.timeout,
        }
        return private_digest(digest_input)

    async def match_request(
        self, project_id: UUID, creator: UUID, agent_id: UUID, request: AgentTrialRequest,
    ) -> AgentTrialReport:
        existing = await self._existing(project_id, creator, request.request_key)
        if existing is None:
            raise HTTPException(404, "未找到试聊报告")
        return await self._reuse(existing, self._request_digest(agent_id, request))

    async def cancel(self, project_id: UUID, creator: UUID, agent_id: UUID, report_id: UUID) -> AgentTrialReport:
        report = await self.get(project_id, creator, agent_id, report_id)
        if report.status == "running":
            await self._finish(report_id, TrialOutcome(
                status="cancelled", error_code="cancelled", error_message="试聊已取消",
            ))
        return await self.get(project_id, creator, agent_id, report_id)

    async def _existing(self, project_id: UUID, creator: UUID, key: UUID) -> AgentTestReport | None:
        row = await self.db.scalar(select(AgentTestReport).where(
            AgentTestReport.project_id == project_id, AgentTestReport.created_by == creator,
            AgentTestReport.request_key == key,
        ))
        if row is None or isinstance(row, AgentTestReport):
            return row
        raise TypeError("Invalid trial report record")

    async def start(
        self, project_id: UUID, creator: UUID, agent_id: UUID, request: AgentTrialRequest,
        *, authorize: Callable[[], Awaitable[None]] | None = None,
    ) -> AgentTrialReport:
        digest = self._request_digest(agent_id, request)
        existing = await self._existing(project_id, creator, request.request_key)
        if existing is not None:
            return await self._reuse(existing, digest)
        agent_service = AgentService(self.db)
        await agent_service.get_agent(project_id, agent_id, enrich_resources=False)
        row = AgentTestReport(
            id=uuid4(), project_id=project_id, created_by=creator, agent_id=agent_id,
            request_key=request.request_key, request_digest=digest, status="running",
            channel=request.channel.value, question=self.redactor.redact(request.message).text,
            answer="", sources=[], expires_at=datetime.now(UTC) + timedelta(seconds=request.timeout + 15),
        )
        self.db.add(row)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            concurrent = await self._existing(project_id, creator, request.request_key)
            if concurrent is None:
                raise HTTPException(409, "无法确认试聊请求状态，请按原请求查询") from None
            return await self._reuse(concurrent, digest)
        report_id = row.id
        execution = asyncio.create_task(self._execute(
            project_id, agent_id, report_id, request, authorize, creator=creator,
        ))
        observation_finished = asyncio.Event()
        watcher = asyncio.create_task(self._watch_terminal(report_id, observation_finished))
        try:
            done, _ = await asyncio.wait(
                {execution, watcher}, timeout=request.timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if execution in done:
                outcome = execution.result()
            elif watcher in done:
                await watcher  # Observation errors stop model work too.
                outcome = TrialOutcome(status="failed", error_code="interrupted", error_message="试聊已停止，请查看原报告")
            else:
                outcome = TrialOutcome(status="failed", error_code="timeout", error_message="试聊超时，请重试")
        except asyncio.CancelledError:
            with CancelScope(shield=True):
                await self._stop(execution, watcher, observation_finished)
                await self._finish(report_id, TrialOutcome(
                    status="cancelled", error_code="cancelled", error_message="试聊已取消",
                ))
            raise
        except HTTPException as exc:
            denied = exc.status_code in {400, 401, 402, 403, 409, 422}
            code = (
                "authorization_denied"
                if denied else "dependency_unavailable"
            )
            message = (
                "试聊授权未通过，请由管理员检查套餐及模型配置"
                if denied else "试聊依赖服务暂时不可用，请稍后重试或联系管理员"
            )
            logger.warning(
                "agent_trial_execution_issue", report_id=str(report_id),
                failure_code=code,
            )
            with CancelScope(shield=True):
                await self._stop(execution, watcher, observation_finished)
                await self._finish(report_id, TrialOutcome(
                    status="blocked" if denied else "failed",
                    error_code=code, error_message=message,
                ))
            raise HTTPException(exc.status_code, message) from None
        except ModelConfigurationVerificationError:
            outcome = TrialOutcome(
                status="blocked", error_code="model_configuration_unavailable",
                error_message="模型配置核验未通过，请由管理员检查模型配置和服务同步状态后重试",
            )
        except AuthorizationError:
            outcome = TrialOutcome(
                status="blocked", error_code="authorization_denied",
                error_message="试聊权限核验未通过，请由管理员检查账号和模型使用权限",
            )
        except ExternalServiceError:
            outcome = TrialOutcome(
                status="failed", error_code="dependency_unavailable",
                error_message="试聊依赖服务暂时不可用，请稍后重试或联系管理员",
            )
        except Exception as exc:
            outcome = TrialOutcome(
                status="failed", error_code="timeout" if isinstance(exc, TimeoutError) else "execution_failed",
                error_message="试聊超时，请重试" if isinstance(exc, TimeoutError) else "试聊未完成，请检查模型和知识配置",
            )
        finally:
            with CancelScope(shield=True):
                await self._stop(execution, watcher, observation_finished)
        if (
            outcome.error_code
            and outcome.error_code != "knowledge_not_verified"
        ):
            logger.warning(
                "agent_trial_execution_issue", report_id=str(report_id),
                failure_code=outcome.error_code,
            )
        await self._finish(report_id, outcome)
        return await self.get(project_id, creator, agent_id, report_id)

    async def _stop(
        self, execution: asyncio.Task[TrialOutcome], watcher: asyncio.Task[None],
        observation_finished: asyncio.Event,
    ) -> None:
        observation_finished.set()
        if not execution.done() and not execution.cancelling():
            execution.cancel()
        # Let the short read-only DB observation close its transaction instead
        # of interrupting driver cleanup and leaving a connection checked out.
        await asyncio.gather(execution, watcher, return_exceptions=True)

    async def _watch_terminal(self, report_id: UUID, finished: asyncio.Event) -> None:
        # Never share the executor's AsyncSession with the polling task.
        sessions = async_sessionmaker(self.db.bind, expire_on_commit=False)
        while not finished.is_set():
            async with sessions() as observer:
                async with asyncio.timeout(5):
                    running = await observer.scalar(select(AgentTestReport.id).where(
                        AgentTestReport.id == report_id, AgentTestReport.status == "running",
                        AgentTestReport.deleted_at.is_(None),
                        AgentTestReport.expires_at > datetime.now(UTC),
                    ))
            if running is None:
                return
            try:
                await asyncio.wait_for(finished.wait(), 0.25)
            except TimeoutError:
                pass

    async def _finish(self, report_id: UUID, outcome: TrialOutcome) -> None:
        # Cancellation during a DB call can leave the executor's transaction
        # unusable. Its task has been awaited before rolling back this session.
        await self.db.rollback()
        conditions = [
            AgentTestReport.id == report_id, AgentTestReport.status == "running",
            AgentTestReport.deleted_at.is_(None),
        ]
        if outcome.status == "completed":
            conditions.append(AgentTestReport.expires_at > datetime.now(UTC))
        values = outcome.model_dump(mode="json")
        if outcome.knowledge_snapshot is None:
            values.pop("knowledge_snapshot")  # Keep the preflight evidence on interruption.
        await self.db.execute(update(AgentTestReport).where(*conditions)
          .values(**values, completed_at=datetime.now(UTC))
          .execution_options(synchronize_session=False))
        await self.db.commit()

    async def _execute(
        self, project_id: UUID, agent_id: UUID, report_id: UUID,
        request: AgentTrialRequest, authorize: Callable[[], Awaitable[None]] | None,
        *, creator: UUID | None = None,
    ) -> TrialOutcome:
        if authorize is not None:
            await authorize()
        async with AIServiceClient(AgentService(self.db), project_id) as loader:
            agent = await loader.get_trial_agent(agent_id, requested_by=creator) if creator is not None else await loader.get_trial_agent(agent_id)
        snapshot = await read_knowledge_snapshot(agent, request.channel)
        changed = await self.db.execute(update(AgentTestReport).where(
            AgentTestReport.id == report_id, AgentTestReport.status == "running",
            AgentTestReport.deleted_at.is_(None),
            AgentTestReport.expires_at > datetime.now(UTC),
        ).values(configuration_fingerprint=configuration_fingerprint(agent),
                 knowledge_snapshot=snapshot.model_dump(mode="json")).returning(AgentTestReport.id)
          .execution_options(synchronize_session=False))
        claimed = changed.scalar_one_or_none()
        await self.db.commit()
        if claimed is None:
            return TrialOutcome(status="cancelled", error_code="cancelled", error_message="试聊已停止")
        result = await self.runtime.run_knowledge_trial(agent, request)
        after = await read_knowledge_snapshot(agent, request.channel)
        snapshot = snapshot.model_copy(update={"verification": compare_snapshots(snapshot, after)})
        evidence = result.knowledge_evidence
        sources: list[TrialKnowledgeSource] = []
        if evidence is not None:
            if evidence.project_id != str(project_id) or evidence.channel != request.channel.value:
                raise ValueError("Knowledge evidence ownership mismatch")
            if evidence.tool_results:
                raise ValueError("Unexpected external action in knowledge trial")
            sources = [TrialKnowledgeSource(
                collection_id=UUID(doc.collection_id), document_id=UUID(doc.document_id),
                file_id=UUID(doc.file_id) if doc.file_id else None,
                content_digest=hashlib.sha256(doc.content.encode()).hexdigest(),
                relevance_score=doc.relevance_score,
            ) for doc in evidence.documents]
            if {row.collection_id for row in sources} - {row.collection_id for row in snapshot.collections}:
                snapshot = snapshot.model_copy(update={"verification": "unknown"})
        knowledge_status = evidence.status if evidence is not None else "unavailable"
        if (
            evidence is not None
            and knowledge_status in {"unavailable", "over_budget"}
        ):
            message = (
                "知识检索服务暂时不可用，请检查向量模型连接和知识库服务后重试"
                if knowledge_status == "unavailable"
                else "检索资料超出核验预算，请缩小问题范围或检查资料分段后重试"
            )
            return TrialOutcome(
                status="blocked", knowledge_status=knowledge_status,
                knowledge_snapshot=snapshot,
                error_code=f"knowledge_{knowledge_status}",
                error_message=message,
            )
        if not result.success:
            return TrialOutcome(status="failed", knowledge_snapshot=snapshot,
                                error_code="generation_failed", error_message="模型没有完成试聊，请检查配置后重试")
        if knowledge_status != "matched":
            return TrialOutcome(
                status="blocked", answer=self.redactor.redact(result.content).text,
                knowledge_status=knowledge_status, sources=sources,
                knowledge_snapshot=snapshot,
                error_code="knowledge_not_verified", error_message="本次没有可核验的匹配资料，不能认定业务能力通过",
            )
        return TrialOutcome(
            status="completed", answer=self.redactor.redact(result.content).text,
            knowledge_status=knowledge_status, sources=sources,
            knowledge_snapshot=snapshot,
        )

    async def _reuse(self, row: AgentTestReport, digest: str) -> AgentTrialReport:
        if row.request_digest != digest or row.deleted_at is not None:
            raise HTTPException(409, "该请求键已用于另一试聊，请查询原报告或创建新请求")
        return await self._report(await self._expire(row))
