"""Owner-read trial applicability; never grants employee activation."""
import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.internal import Agent
from app.runtime.supervisor.infrastructure.services import AIServiceClient
from app.schemas.agent_trials import (
    AgentTrialReport, TrialApplicability, TrialKnowledgeRevision, TrialKnowledgeSnapshot,
)
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_service import AgentService
from app.services.agent_trial_fingerprint import configuration_fingerprint
from app.services.default_knowledge import agent_knowledge_availability


async def read_knowledge_snapshot(agent: Agent, channel: KnowledgeChannel | str) -> TrialKnowledgeSnapshot:
    """Missing old-owner revision and owner outages remain unknown."""
    channel = KnowledgeChannel(channel)
    try:
        async with asyncio.timeout(3):
            available = await agent_knowledge_availability(agent, channel)
        if (str(available.project_id) != agent.project_id or available.agent_id != agent.id
                or available.channel != channel):
            raise ValueError("Knowledge ownership mismatch")
        issues = list(available.issues)
        if any(row.resource_revision is None for row in available.collections):
            issues.append("missing_revision")
        if not available.collections and not issues:
            issues.append("no_binding")
        return TrialKnowledgeSnapshot(
            channel=channel, binding_mode=available.binding_mode, captured_at=datetime.now(UTC),
            collections=[TrialKnowledgeRevision(collection_id=row.id, resource_revision=row.resource_revision)
                         for row in sorted(available.collections, key=lambda row: str(row.id))],
            issues=sorted(set(issues)),
        )
    except Exception:
        # Never expose provider exceptions or invent revisions from old counts.
        return TrialKnowledgeSnapshot(
            channel=channel, binding_mode="unbound", captured_at=datetime.now(UTC),
            collections=[], issues=["knowledge_unavailable"],
        )


def compare_snapshots(before: TrialKnowledgeSnapshot, after: TrialKnowledgeSnapshot) -> str:
    if before.issues or after.issues or any(
        row.resource_revision is None for row in [*before.collections, *after.collections]
    ):
        return "unknown"
    def identity(value: TrialKnowledgeSnapshot) -> tuple[str, str, tuple[tuple[str, str | None], ...]]:
        return (value.channel, value.binding_mode, tuple(sorted(
            (str(row.collection_id), row.resource_revision) for row in value.collections
        )))
    return "consistent" if identity(before) == identity(after) else "changed"


class AgentTrialDependencies:
    def __init__(self, db: AsyncSession | None) -> None:
        self.db = db

    async def current(
        self, project_id: UUID, agent_id: UUID, channel: KnowledgeChannel, *, requested_by: UUID | None = None,
    ) -> tuple[str, TrialKnowledgeSnapshot]:
        if self.db is None:
            raise RuntimeError("Dependency reader requires a database")
        # A fresh identity map sees edits made while the original request ran.
        sessions = async_sessionmaker(self.db.bind, expire_on_commit=False)
        async with asyncio.timeout(5), sessions() as db:
            async with AIServiceClient(AgentService(db), project_id) as loader:
                agent = await loader.get_trial_agent(
                    agent_id, requested_by=requested_by, verify_runtime=False,
                ) if requested_by is not None else await loader.get_trial_agent(agent_id)
            return configuration_fingerprint(agent), await read_knowledge_snapshot(agent, channel)

    async def check(
        self, report: AgentTrialReport, project_id: UUID,
        cache: dict[KnowledgeChannel, tuple[str, TrialKnowledgeSnapshot] | None] | None = None,
        *, allow_safe_refusal: bool = False,
        requested_by: UUID | None = None,
    ) -> TrialApplicability:
        checked_at = datetime.now(UTC)
        saved = report.knowledge_snapshot
        safe_refusal = allow_safe_refusal and report.status == "blocked" and report.knowledge_status == "no_match"
        if report.status != "completed" and not safe_refusal:
            return TrialApplicability(status="not_checked", issues=["trial_not_completed"])
        if saved is None or report.configuration_fingerprint is None:
            return TrialApplicability(issues=["snapshot_not_recorded"])
        if saved.verification == "changed":
            return TrialApplicability(status="changed", checked_at=checked_at, issues=["changed_during_trial"])
        if saved.verification != "consistent" or saved.issues:
            return TrialApplicability(status="unknown", checked_at=checked_at, issues=["revision_unconfirmed"])
        if cache is None:
            cache = {}
        if report.channel not in cache:
            try:
                if requested_by is None and self.db is not None:
                    from app.models.agent_test_report import AgentTestReport
                    from sqlalchemy import select
                    requested_by = await self.db.scalar(select(AgentTestReport.created_by).where(
                        AgentTestReport.id == report.id, AgentTestReport.project_id == project_id,
                        AgentTestReport.agent_id == report.agent_id, AgentTestReport.deleted_at.is_(None),
                    ))
                cache[report.channel] = await self.current(
                    project_id, report.agent_id, report.channel, requested_by=requested_by,
                )
            except Exception:
                cache[report.channel] = None
        current = cache[report.channel]
        if current is None:
            return TrialApplicability(status="unknown", checked_at=checked_at, issues=["agent_unavailable"])
        fingerprint, snapshot = current
        if fingerprint != report.configuration_fingerprint:
            return TrialApplicability(status="changed", checked_at=checked_at, issues=["configuration_changed"])
        comparison = compare_snapshots(saved, snapshot)
        if comparison == "unknown":
            return TrialApplicability(status="unknown", checked_at=checked_at, issues=["knowledge_unavailable"])
        if comparison == "changed":
            return TrialApplicability(status="changed", checked_at=checked_at, issues=["knowledge_changed"])
        return TrialApplicability(status="current", checked_at=checked_at)
