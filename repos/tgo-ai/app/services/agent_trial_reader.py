"Read trial evidence without creating runtime or expiring/committing \
reports."

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_test_report import AgentTestReport
from app.schemas.agent_trials import AgentTrialReport
from app.services.agent_trial_dependencies import AgentTrialDependencies


class AgentTrialReader:
    def __init__(self, db: AsyncSession) -> None:
        self.db, self.dependencies = db, AgentTrialDependencies(db)

    async def get(
        self, project_id: UUID, creator: UUID, agent_id: UUID, report_id: UUID
    ) -> AgentTrialReport:
        row = await self.db.scalar(
            select(AgentTestReport)
            .where(
                AgentTestReport.id == report_id,
                AgentTestReport.project_id == project_id,
                AgentTestReport.created_by == creator,
                AgentTestReport.agent_id == agent_id,
                AgentTestReport.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise HTTPException(404, "未找到试聊报告")
        report = AgentTrialReport.model_validate(row)
        applicability = await self.dependencies.check(
            report, project_id, requested_by=creator
        )
        return report.model_copy(update={"applicability": applicability})
