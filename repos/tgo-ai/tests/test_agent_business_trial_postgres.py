"""Real PostgreSQL proves migration rollback guard and one concurrent dispatch."""

import asyncio
import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import SecretStr
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Agent, BaseModel
from app.models.agent_business_connection import AgentBusinessConnection
from app.models.agent_business_trial import AgentBusinessTrial
from app.schemas.agent_business_connections import BusinessConnectionOwner
from app.schemas.agent_business_trials import (
    BusinessTrialRequest,
    BusinessTrialCheck,
    BusinessTrialReceipt,
    BusinessTrialCustomerEvidence,
)
from app.schemas.agent_configuration_actor import AgentConfigurationActor
from app.services.agent_business_trials import AgentBusinessTrialService
from app.models.agent_business_review import AgentBusinessReview
from app.models.agent_business_review_request import AgentBusinessReviewRequest
from app.services.agent_business_review_requests import AgentBusinessReviewRequests
from app.schemas.agent_business_reviews import (
    BusinessReviewRequest,
    BusinessReviewChecks,
    BusinessTestEvidence,
)
from app.services.agent_business_reviews import AgentBusinessReviewService
from app.services.agent_business_capabilities import AgentBusinessCapabilityService
from app.exceptions import ConflictError

URL = os.getenv("AI_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="Explicit owned isolated PostgreSQL required"
)


@pytest.mark.asyncio
async def test_business_report_migration_and_concurrent_dispatch():
    assert URL
    url = make_url(URL)
    assert url.host in {"localhost", "127.0.0.1"} and url.port == 15432
    assert (url.database or "").startswith("tgo_scenarios_test_")
    schema = "business_trial_" + uuid4().hex
    admin = create_async_engine(URL)
    engine = create_async_engine(
        URL, connect_args={"server_settings": {"search_path": schema}}
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    path = (
        Path(__file__).parents[1]
        / "migrations/versions/z8a9b0c1d2e3_agent_business_trials.py"
    )
    spec = importlib.util.spec_from_file_location("business_trial_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def migrate(connection, method):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration, method)()

    owner = AsyncMock()
    owner.get_agent_configuration_actor.side_effect = lambda q: AgentConfigurationActor(
        **q.model_dump(), checked_at=datetime.now(UTC)
    )
    proof_id, proof_until = uuid4(), datetime.now(UTC) + timedelta(hours=1)
    owner.get_agent_business_connection.side_effect = lambda q: BusinessConnectionOwner(
        **q.model_dump(),
        name="隔离商户",
        mode="http",
        enabled=True,
        operations=["order_query"],
        revision=1,
        configuration_revision=1,
        authorization_revision=1,
        resource_revision="a" * 64,
        test_status="passed",
        configuration_tests=[
            {
                "operation": "order_query",
                "report_id": proof_id,
                "status": "current",
                "valid_until": proof_until,
                "issues": [],
            }
        ],
        checked_at=datetime.now(UTC),
    )
    fingerprint = AsyncMock(return_value="b" * 64)
    project, actor, agent, connection = [uuid4() for _ in range(4)]
    request = BusinessTrialRequest(
        request_id=uuid4(),
        connection_id=connection,
        operation="order_query",
        visitor_id=uuid4(),
        order_no=SecretStr("ISOLATED-ORDER-123"),
        customer_session=SecretStr("isolated-customer-session"),
    )
    try:
        async with admin.begin() as db:
            await db.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as db:
            await db.run_sync(
                lambda sync: BaseModel.metadata.create_all(
                    sync,
                    tables=[
                        table
                        for name, table in BaseModel.metadata.tables.items()
                        if name
                        not in {
                            AgentBusinessTrial.__tablename__,
                            "ai_agent_business_reviews",
                            "ai_agent_business_review_requests",
                        }
                    ],
                )
            )
            for method in ("upgrade", "downgrade", "upgrade"):
                await db.run_sync(migrate, method)
            columns = await db.run_sync(
                lambda sync: inspect(sync).get_columns(AgentBusinessTrial.__tablename__)
            )
            assert {row["name"] for row in columns} == set(
                AgentBusinessTrial.__table__.columns.keys()
            )
            for row in columns:
                assert (
                    row["nullable"]
                    == AgentBusinessTrial.__table__.columns[row["name"]].nullable
                )
        async with sessions() as db:
            db.add(
                Agent(
                    id=agent,
                    project_id=project,
                    name="隔离订单测试",
                    model="fixture",
                    is_active=False,
                )
            )
            db.add(
                AgentBusinessConnection(
                    project_id=project,
                    agent_id=agent,
                    connection_id=connection,
                    operations=["order_query"],
                    enabled=True,
                    revision=1,
                    configuration_revision=1,
                    authorization_revision=1,
                    resource_revision="a" * 64,
                    configured_by=actor,
                )
            )
            await db.commit()

        async def prepare():
            async with sessions() as db:
                return await AgentBusinessTrialService(
                    db, owner=owner, fingerprint=fingerprint
                ).prepare(project, actor, agent, request)

        preparation_results = await asyncio.gather(
            prepare(), prepare(), return_exceptions=True
        )
        completed = [
            item for item in preparation_results if not isinstance(item, BaseException)
        ]
        assert completed and all(
            not isinstance(item, BaseException) or isinstance(item, ConflictError)
            for item in preparation_results
        )
        first = completed[0]
        duplicate = await prepare()
        assert (
            first[0].id == duplicate[0].id
            and sum(value[1] is not None for value in completed) == 1
        )
        command = first[1] or duplicate[1]
        assert command is not None

        async def query(value):
            async with sessions() as db:
                await AgentBusinessTrialService(
                    db, owner=owner, fingerprint=fingerprint
                ).authorize(BusinessTrialCheck(command=value, phase="before_query"))
            await asyncio.sleep(0.02)
            return BusinessTrialReceipt(
                **value.identity(),
                outcome="passed",
                summary={"status": "已发货"},
                error_code=None,
                checked_at=datetime.now(UTC),
                customer_evidence=BusinessTrialCustomerEvidence(
                    binding_id=uuid4(),
                    binding_revision=1,
                    policy_revision=1,
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                ),
            )

        owner.query_agent_business_trial.side_effect = query

        async def run():
            async with sessions() as db:
                await AgentBusinessTrialService(
                    db, owner=owner, fingerprint=fingerprint
                ).run(command)

        await asyncio.gather(run(), run())
        owner.query_agent_business_trial.assert_awaited_once()
        async with sessions() as db:
            result = await AgentBusinessTrialService(
                db, owner=owner, fingerprint=fingerprint
            ).by_request(project, actor, agent, request.request_id)
            assert (
                result.status == "completed"
                and result.receipt.outcome == "passed"
                and not result.can_enable
            )
        with pytest.raises(RuntimeError, match="reports exist"):
            async with engine.begin() as db:
                await db.run_sync(migrate, "downgrade")
        review_path = (
            Path(__file__).parents[1]
            / "migrations/versions/a9b0c1d2e3f4_agent_business_reviews.py"
        )
        review_spec = importlib.util.spec_from_file_location(
            "business_review_migration", review_path
        )
        assert review_spec and review_spec.loader
        review_migration = importlib.util.module_from_spec(review_spec)
        review_spec.loader.exec_module(review_migration)

        def review_upgrade(connection, method):
            with Operations.context(MigrationContext.configure(connection)):
                getattr(review_migration, method)()

        async with engine.begin() as db:
            for method in ("upgrade", "downgrade", "upgrade"):
                await db.run_sync(review_upgrade, method)
            columns = await db.run_sync(
                lambda sync: inspect(sync).get_columns(
                    AgentBusinessReview.__tablename__
                )
            )
            assert {item["name"] for item in columns} == set(
                AgentBusinessReview.__table__.columns.keys()
            )
            for item in columns:
                assert (
                    item["nullable"]
                    == AgentBusinessReview.__table__.columns[item["name"]].nullable
                )
        intention_path = (
            Path(__file__).parents[1]
            / "migrations/versions/c1d2e3f4a5b6_business_review_requests.py"
        )
        intention_spec = importlib.util.spec_from_file_location(
            "business_review_request_migration", intention_path
        )
        assert intention_spec and intention_spec.loader
        intention_migration = importlib.util.module_from_spec(intention_spec)
        intention_spec.loader.exec_module(intention_migration)

        def intention_upgrade(connection, method):
            with Operations.context(MigrationContext.configure(connection)):
                getattr(intention_migration, method)()

        async with engine.begin() as db:
            for method in ("upgrade", "downgrade", "upgrade"):
                await db.run_sync(intention_upgrade, method)
            columns = await db.run_sync(
                lambda sync: inspect(sync).get_columns(
                    AgentBusinessReviewRequest.__tablename__
                )
            )
            assert {item["name"] for item in columns} == set(
                AgentBusinessReviewRequest.__table__.columns.keys()
            )
            assert all(
                item["nullable"]
                == AgentBusinessReviewRequest.__table__.columns[item["name"]].nullable
                for item in columns
            )
        owner.get_agent_business_test_evidence.side_effect = lambda q: (
            BusinessTestEvidence(
                **q.model_dump(),
                status="current",
                issues=[],
                checked_at=datetime.now(UTC),
            )
        )
        approve = BusinessReviewRequest(
            request_id=uuid4(),
            expected_latest_review_id=None,
            decision="passed",
            reason=None,
            checks=BusinessReviewChecks(
                ownership_checked=True, result_checked=True, readonly_scope_checked=True
            ),
        )

        async def judge(value):
            async with sessions() as db:
                trials = AgentBusinessTrialService(
                    db, owner=owner, fingerprint=fingerprint
                )
                return await AgentBusinessReviewService(db, trials).review(
                    project, actor, agent, command.report_id, value
                )

        entered, release = asyncio.Event(), asyncio.Event()
        original_evidence = owner.get_agent_business_test_evidence.side_effect

        async def delayed_evidence(query):
            entered.set()
            await release.wait()
            return original_evidence(query)

        owner.get_agent_business_test_evidence.side_effect = delayed_evidence
        cancelled_command = approve.model_copy(update={"request_id": uuid4()})
        pending_task = asyncio.create_task(judge(cancelled_command))
        await asyncio.wait_for(entered.wait(), 3)
        async with sessions() as db:
            requests = AgentBusinessReviewRequests(
                AgentBusinessReviewService(
                    db,
                    AgentBusinessTrialService(db, owner=owner, fingerprint=fingerprint),
                )
            )
            state = await requests.state(
                project, actor, agent, command.report_id, cancelled_command.request_id
            )
            assert state.processing and not state.can_resume
            cancelled = await requests.cancel(
                project, actor, agent, command.report_id, cancelled_command.request_id
            )
            assert cancelled.status == "cancelled" and cancelled.result is None
        release.set()
        with pytest.raises(ConflictError):
            await pending_task
        owner.get_agent_business_test_evidence.side_effect = original_evidence
        judgments = await asyncio.gather(
            judge(approve), judge(approve), return_exceptions=True
        )
        successful = [item for item in judgments if not isinstance(item, BaseException)]
        assert successful and all(
            not isinstance(item, BaseException) or isinstance(item, ConflictError)
            for item in judgments
        )
        first_review, duplicate_review = successful[0], await judge(approve)
        assert first_review == duplicate_review and first_review.revision == 1
        reject = BusinessReviewRequest(
            request_id=uuid4(),
            expected_latest_review_id=first_review.id,
            decision="rejected",
            reason="wrong_result",
            checks=BusinessReviewChecks(),
        )
        competing = reject.model_copy(
            update={"request_id": uuid4(), "reason": "scope_mismatch"}
        )
        results = await asyncio.gather(
            judge(reject), judge(competing), return_exceptions=True
        )
        assert sum(isinstance(item, ConflictError) for item in results) == 1
        async with sessions() as db:
            reviews = AgentBusinessReviewService(
                db, AgentBusinessTrialService(db, owner=owner, fingerprint=fingerprint)
            )
            recovered = await reviews.request_result(
                project, actor, agent, command.report_id, approve.request_id
            )
            assert recovered == first_review
            state = await AgentBusinessCapabilityService(db, reviews).read(
                project, actor, agent
            )
            assert state.capabilities[0].status == "rejected" and not state.can_execute
        with pytest.raises(RuntimeError, match="reviews exist"):
            async with engine.begin() as db:
                await db.run_sync(review_upgrade, "downgrade")
        with pytest.raises(RuntimeError, match="receipts exist"):
            async with engine.begin() as db:
                await db.run_sync(intention_upgrade, "downgrade")
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
