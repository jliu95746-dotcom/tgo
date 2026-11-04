"""Bounded latest-evidence reads see committed changes in a real owned database."""

import os
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Agent, BaseModel, Project
from app.models.agent_business_connection import AgentBusinessConnection
from app.models.agent_business_trial import AgentBusinessTrial
from app.models.agent_business_review import AgentBusinessReview
from app.services.agent_business_evidence_revision import business_evidence_revision

TEST_URL = os.getenv("AI_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="An isolated PostgreSQL database is required")


@pytest.mark.asyncio
async def test_current_business_revision_is_scope_bound_and_refreshes_committed_judgments():
    assert TEST_URL and re.fullmatch(r"tgo_scenarios_test_[0-9a-f]{32}", make_url(TEST_URL).database or "")
    engine = create_async_engine(TEST_URL, echo=False)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project, actor, agent, connection, report = [uuid4() for _ in range(5)]
    now = datetime.now(UTC)
    try:
        async with engine.begin() as db:
            await db.run_sync(BaseModel.metadata.create_all)
        async with sessions() as db:
            db.add(Project(id=project, name="业务依据隔离验证", api_key="synthetic_" + uuid4().hex))
            db.add(Agent(id=agent, project_id=project, name="隔离员工", model="fixture", is_active=False))
            await db.flush()
            binding = AgentBusinessConnection(project_id=project, agent_id=agent,
                connection_id=connection, operations=["order_query", "logistics_query"], enabled=True,
                revision=1, configuration_revision=1, authorization_revision=1,
                resource_revision="a" * 64, configured_by=actor)
            db.add(binding)
            await db.flush()
            row = AgentBusinessTrial(id=report, project_id=project, actor_id=actor, agent_id=agent,
                connection_id=connection, request_id=uuid4(), attempt_id=uuid4(), operation="order_query",
                binding_id=binding.id, binding_revision=1, configuration_fingerprint="b" * 64,
                expected_revision=1, configuration_revision=1, authorization_revision=1,
                resource_revision="a" * 64, configuration_test_id=uuid4(),
                configuration_test_valid_until=now + timedelta(hours=1), command_digest="c" * 64,
                status="completed", expires_at=now + timedelta(minutes=3), completed_at=now,
                receipt_json='{"outcome":"passed"}', created_at=now)
            db.add(row)
            await db.commit()
            before = await business_evidence_revision(db, project, actor, agent)
            assert before and before == await business_evidence_revision(db, project, actor, agent)
            assert await business_evidence_revision(db, uuid4(), actor, agent) is None
            async with sessions() as other:
                other.add(AgentBusinessReview(project_id=project, reviewed_by=actor, agent_id=agent,
                    report_id=report, connection_id=connection, operation="order_query", request_id=uuid4(),
                    revision=1, request_digest="d" * 64, report_digest="e" * 64,
                    configuration_fingerprint="b" * 64, decision="passed", result_json="{}",
                    valid_until=now + timedelta(hours=1)))
                await other.commit()
            reviewed = await business_evidence_revision(db, project, actor, agent)
            assert reviewed != before
            async with sessions() as other:
                other.add(AgentBusinessReview(project_id=project, reviewed_by=uuid4(), agent_id=agent,
                    report_id=report, connection_id=connection, operation="order_query", request_id=uuid4(),
                    revision=2, request_digest="d" * 64, report_digest="e" * 64,
                    configuration_fingerprint="b" * 64, decision="rejected", result_json="{}",
                    valid_until=now + timedelta(hours=1)))
                await other.commit()
            assert await business_evidence_revision(db, project, actor, agent) == reviewed
            async with sessions() as other:
                other.add(AgentBusinessReview(project_id=project, reviewed_by=actor, agent_id=agent,
                    report_id=report, connection_id=connection, operation="order_query", request_id=uuid4(),
                    revision=3, request_digest="d" * 64, report_digest="e" * 64,
                    configuration_fingerprint="b" * 64, decision="rejected", result_json="{}",
                    valid_until=now + timedelta(hours=1)))
                await other.commit()
            assert await business_evidence_revision(db, project, actor, agent) != reviewed
    finally:
        await engine.dispose()
