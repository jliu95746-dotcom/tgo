"""Real row locks, scoped ownership and reversible review migrations."""
import asyncio
import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Agent, AgentTestReport, AgentTrialReview, BaseModel, Project
from app.schemas.agent_trial_reviews import KnowledgeReviewCase, KnowledgeReviewChecks, KnowledgeReviewCommand
from app.schemas.agent_trials import AgentTrialReport, TrialApplicability, TrialKnowledgeRevision, TrialKnowledgeSnapshot
from app.services.agent_trial_reviews import AgentTrialReviewService

TEST_URL = os.getenv("AI_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="An isolated local PostgreSQL test database is required")


@pytest.mark.asyncio
async def test_review_migration_real_concurrent_retries_and_stale_judgments():
    assert TEST_URL and (make_url(TEST_URL).database or "").startswith("tgo_scenarios_test_")
    assert make_url(TEST_URL).host in {"localhost", "127.0.0.1"}
    schema = "trial_reviews_" + uuid4().hex
    admin = create_async_engine(TEST_URL)
    engine = create_async_engine(TEST_URL, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    migration_path = Path(__file__).parents[1] / "migrations/versions/u3v4w5x6y7z8_agent_trial_reviews.py"
    spec = importlib.util.spec_from_file_location("trial_review_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    created = False
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True

        def apply(connection, action):
            with Operations.context(MigrationContext.configure(connection)):
                getattr(migration, action)()

        def initialize(connection):
            BaseModel.metadata.create_all(connection, tables=[
                table for table in BaseModel.metadata.sorted_tables if table is not AgentTrialReview.__table__
            ])
            apply(connection, "upgrade")

        async with engine.begin() as connection:
            await connection.run_sync(initialize)
        project, actor, agent, serving, report_id = [uuid4() for _ in range(5)]
        case = KnowledgeReviewCase(id="knowledge.v1.1", template_id="knowledge", template_version=1,
            question="这款包是什么材质？", expected_result="grounded_answer")
        snapshot = TrialKnowledgeSnapshot(channel="web", binding_mode="explicit", verification="consistent",
            captured_at=datetime.now(UTC), collections=[TrialKnowledgeRevision(collection_id=uuid4(), resource_revision="a"*64)])
        async with sessions() as db:
            db.add(Project(id=project, name="隔离评审测试", api_key="synthetic_"+uuid4().hex))
            db.add_all([Agent(id=serving, project_id=project, name="接待员工", model="test", is_active=True, is_default=True),
                        Agent(id=agent, project_id=project, name="待测试员工", model="test", is_active=False, is_default=False)])
            db.add(AgentTestReport(id=report_id, project_id=project, created_by=actor, agent_id=agent,
                request_key=uuid4(), request_digest="b"*64, status="completed", channel="web", question=case.question,
                answer="依据测试资料为牛皮", configuration_fingerprint="c"*64, knowledge_status="matched",
                knowledge_snapshot=snapshot.model_dump(mode="json"), expires_at=datetime.now(UTC)+timedelta(minutes=1),
                completed_at=datetime.now(UTC), sources=[{"collection_id": str(snapshot.collections[0].collection_id),
                    "document_id": str(uuid4()), "file_id": None, "content_digest": "d"*64, "relevance_score": 0.9}]))
            await db.commit()

        def service(db):
            async def owned_get(p, staff, employee, identifier):
                row = await db.scalar(select(AgentTestReport).where(AgentTestReport.id == identifier,
                    AgentTestReport.project_id == p, AgentTestReport.created_by == staff, AgentTestReport.agent_id == employee,
                    AgentTestReport.deleted_at.is_(None)))
                if row is None:
                    raise HTTPException(404, "未找到试聊报告")
                return AgentTrialReport.model_validate(row).model_copy(update={"applicability": TrialApplicability(status="current")})
            trials = Mock(get=owned_get)
            trials.dependencies.check = AsyncMock(return_value=TrialApplicability(status="current"))
            return AgentTrialReviewService(db, trials)

        checks = KnowledgeReviewChecks(answer_correct=True, evidence_checked=True, no_unverified_claims=True, expected_behavior=True)
        command = KnowledgeReviewCommand(request_key=uuid4(), case_id=case.id, case=case, decision="passed", checks=checks)

        async def submit(payload):
            async with sessions() as db:
                return await service(db).review(project, actor, agent, report_id, payload)

        retries = await asyncio.wait_for(asyncio.gather(submit(command), submit(command)), 20)
        assert retries[0].id == retries[1].id and retries[0].revision == 1
        async with sessions() as db:
            assert len((await db.scalars(select(AgentTrialReview))).all()) == 1
            assert (await db.get(Agent, serving)).is_active is True
            assert (await db.get(Agent, agent)).is_active is False
            for foreign_project, foreign_actor in [(uuid4(), actor), (project, uuid4())]:
                with pytest.raises(HTTPException) as failure:
                    await service(db).state(foreign_project, foreign_actor, agent, report_id, [case])
                assert failure.value.status_code == 404
        # A second independent operation based on the same old decision conflicts.
        changes = [command.model_copy(update={"request_key": uuid4(), "expected_latest_review_id": retries[0].id,
            "decision": "rejected", "note": str(index)}) for index in range(2)]
        outcomes = await asyncio.wait_for(asyncio.gather(*(submit(change) for change in changes), return_exceptions=True), 20)
        assert len([result for result in outcomes if isinstance(result, HTTPException) and result.status_code == 409]) == 1
        accepted = [result for result in outcomes if not isinstance(result, BaseException)][0]
        assert accepted.revision == 2
        async with sessions() as db:
            state = await service(db).state(project, actor, agent, report_id, [case])
            assert [row.revision for row in state.reviews] == [2, 1]
            assert not any(row.effective for row in state.reviews)
            assert state.reviews[1].superseded and state.reviews[1].decision == "passed"
        # Downgrade only drops reviews; original report/employee data survives.
        async with engine.begin() as connection:
            await connection.run_sync(apply, "downgrade")
            assert (await connection.execute(text("SELECT answer FROM ai_agent_test_reports WHERE id=:id"), {"id": report_id})).scalar_one() == "依据测试资料为牛皮"
            await connection.run_sync(apply, "upgrade")
            assert (await connection.execute(text("SELECT count(*) FROM ai_agent_trial_reviews"))).scalar_one() == 0
    finally:
        await engine.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
