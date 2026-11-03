"""Independent PostgreSQL transactions share one trial permit and settlement."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.models import Project, Staff
from app.models.ai_usage import AIUsageMovement, AIUsageReservation
from app.models.company_account import AICreditBatch, CompanyAccount
from app.schemas.agent_trials import AgentTrialReport, AgentTrialRequest
from app.services import agent_trial_usage as usage

TEST_URL = os.getenv("SCENARIOS_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="An isolated PostgreSQL test database is required")


def test_concurrent_reservation_and_settlement_are_atomic(monkeypatch):
    assert TEST_URL is not None
    url = make_url(TEST_URL).set(drivername="postgresql+psycopg2")
    assert (url.database or "").startswith("tgo_scenarios_test_")
    schema = "trial_usage_" + uuid4().hex
    administrative = create_engine(url)
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with administrative.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        tables = {model.__table__ for model in (Project, Staff, CompanyAccount, AICreditBatch, AIUsageReservation, AIUsageMovement)}
        pending = list(tables)
        while pending:
            for key in pending.pop().foreign_keys:
                if key.column.table not in tables:
                    tables.add(key.column.table)
                    pending.append(key.column.table)
        with engine.begin() as connection:
            Base.metadata.create_all(connection, tables=list(tables))
        factory = sessionmaker(engine)
        monkeypatch.setattr(usage, "SessionLocal", factory)
        monkeypatch.setattr(settings, "SAAS_ENABLED", True)
        monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
        project, staff, agent = uuid4(), uuid4(), uuid4()
        with factory.begin() as db:
            db.add(Project(id=project, name="隔离试聊计费测试", api_key="synthetic"))
            db.flush()
            db.add(Staff(id=staff, project_id=project, username="synthetic", password_hash="unused", role="admin"))
            db.add(CompanyAccount(project_id=project, status="trial", seat_limit=1, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
            db.add(AICreditBatch(project_id=project, source_key="synthetic-trial-credit", kind="trial", amount=2,
                                 remaining=2, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        request = AgentTrialRequest(request_key=uuid4(), message="材质？")
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = [executor.submit(usage.reserve_trial, project, staff, agent, request) for _ in range(2)]
            permits = [future.result(timeout=20) for future in claims]
        assert permits[0] == permits[1] and permits[0] is not None
        report = AgentTrialReport(id=uuid4(), agent_id=agent, request_key=request.request_key, status="completed",
                                  channel="web", question=request.message, answer="真实数据库测试，模型为替身。",
                                  created_at=datetime.now(timezone.utc))
        with ThreadPoolExecutor(max_workers=2) as executor:
            settlements = [executor.submit(usage.finish_trial, project, staff, report) for _ in range(2)]
            for future in settlements:
                future.result(timeout=20)
        with Session(engine) as db:
            assert len(list(db.scalars(select(AIUsageReservation)))) == 1
            assert db.scalar(select(AICreditBatch.remaining)) == 1
            assert len(list(db.scalars(select(AIUsageMovement)))) == 2
            row = db.scalar(select(AIUsageReservation))
            assert row.status == "settled" and row.receipt["kind"] == "agent_trial"
        recovered_request = request.model_copy(update={"request_key": uuid4()})
        usage.reserve_trial(project, staff, agent, recovered_request)
        recovered_report = report.model_copy(update={"id": uuid4(), "request_key": recovered_request.request_key})
        with Session(engine) as db:
            row = db.scalar(select(AIUsageReservation).where(AIUsageReservation.round_key == f"trial:{staff}:{recovered_request.request_key}"))
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        # An expired lease with a persisted report is settled, never refunded.
        monkeypatch.setattr(usage.ai_client, "agent_trial_by_request", AsyncMock(return_value=recovered_report))
        asyncio.run(usage.recover_trials())
        with Session(engine) as db:
            assert set(db.scalars(select(AIUsageReservation.status))) == {"settled"}
            assert db.scalar(select(AICreditBatch.remaining)) == 0
            assert len(list(db.scalars(select(AIUsageMovement)))) == 4
    finally:
        engine.dispose()
        with administrative.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        administrative.dispose()
