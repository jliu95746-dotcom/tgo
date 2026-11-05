"""Independent PostgreSQL workers must not double-claim or double-charge."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.core.exceptions import TGOAPIException
from app.models import Project, Staff
from app.models.ai_usage import AIUsageMovement, AIUsageReservation
from app.models.company_account import AICreditBatch, CompanyAccount
from app.schemas.workflow_ai_usage import (
    WorkflowAIResult,
    WorkflowUsageOutcome,
)
from app.services import workflow_ai_usage as usage
from tests.test_workflow_ai_usage import call_for

TEST_URL = os.getenv("SCENARIOS_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="Isolated PostgreSQL is required"
)


def test_concurrent_claim_and_settlement_use_one_credit(monkeypatch):
    assert TEST_URL is not None
    url = make_url(TEST_URL).set(drivername="postgresql+psycopg2")
    assert (url.database or "").startswith("tgo_scenarios_test_")
    schema = "workflow_usage_" + uuid4().hex
    admin = create_engine(url)
    engine = create_engine(
        url,
        connect_args={
            "options": f"-csearch_path={schema} -clock_timeout=10000"
        },
    )
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        tables = {
            model.__table__
            for model in (
                Project,
                Staff,
                CompanyAccount,
                AICreditBatch,
                AIUsageReservation,
                AIUsageMovement,
            )
        }
        pending = list(tables)
        while pending:
            for reference in pending.pop().foreign_keys:
                if reference.column.table not in tables:
                    tables.add(reference.column.table)
                    pending.append(reference.column.table)
        with engine.begin() as connection:
            Base.metadata.create_all(connection, tables=list(tables))
        factory = sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(usage, "SessionLocal", factory)
        monkeypatch.setattr(settings, "SAAS_ENABLED", True)
        monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
        with factory.begin() as db:
            project = Project(name="隔离工作流计费测试", api_key="synthetic")
            db.add(project)
            db.flush()
            staff = Staff(
                project_id=project.id,
                username="synthetic",
                password_hash="unused",
                role="admin",
            )
            db.add(staff)
            db.flush()
            expiry = datetime.now(timezone.utc) + timedelta(days=1)
            db.add(
                CompanyAccount(
                    project_id=project.id,
                    status="trial",
                    seat_limit=1,
                    expires_at=expiry,
                )
            )
            db.add(
                AICreditBatch(
                    project_id=project.id,
                    source_key="synthetic-workflow",
                    kind="trial",
                    amount=1,
                    remaining=1,
                    expires_at=expiry,
                )
            )
        command, context = call_for(staff)

        def claim():
            try:
                return usage.claim(command, context)
            except TGOAPIException as error:
                assert error.status_code == 409
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(claim) for _ in range(2)]
            claims = [future.result(timeout=20) for future in futures]
        assert sum(claim is not None for claim in claims) == 1
        claimed = next(claim for claim in claims if claim is not None)
        result = WorkflowAIResult(
            success=True, content="合成答复，不调用模型。", execution_id=uuid4()
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(usage.complete, claimed, result)
                for _ in range(2)
            ]
            for future in futures:
                future.result(timeout=20)
        outcome = WorkflowUsageOutcome(
            project_id=staff.project_id,
            execution_id=command.execution_id,
            status="completed",
            output="合成答复",
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(usage.finish, claimed.permit, outcome)
                for _ in range(2)
            ]
            for future in futures:
                future.result(timeout=20)
        with factory() as db:
            assert list(db.scalars(select(AIUsageReservation.status))) == [
                "settled"
            ]
            assert db.scalar(select(AICreditBatch.remaining)) == 0
            assert len(list(db.scalars(select(AIUsageMovement)))) == 2
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(
                text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            )
        admin.dispose()
