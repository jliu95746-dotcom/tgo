"""Review the original retained result with real persistence and migration."""

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from app.exceptions import ConflictError, ExternalServiceError, NotFoundError
from app.models.agent_device_script_review import (
    AgentDeviceScriptReview,
)
from app.schemas.agent_device_script_requests import ScriptTrialControl
from app.schemas.agent_device_script_reviews import ScriptReviewWrite
from app.services import agent_device_script_reviews
from app.services.agent_device_script_reviews import (
    AgentDeviceScriptReviews,
)
from tests.test_agent_device_script_binding import (  # noqa: F401
    database as binding_database,
)
from tests.test_agent_device_script_cases import (
    PRIVATE,
    command,
    service,
)
from tests.test_agent_device_script_cases import (  # noqa: F401
    evidence_key,
)
from tests.test_agent_device_script_trials import trial_db  # noqa: F401


@pytest.mark.asyncio
async def test_review_fixed_result_and_preserve_revocation(
    trial_db,  # noqa: F811
    monkeypatch,  # noqa: F811
):
    (
        sessions,
        project,
        actor,
        agent,
        _,
        owner,
        binding,
        _,
        engine,
    ) = trial_db
    path = Path(__file__).parents[1] / (
        "migrations/versions/l0a1b2c3d4e5_script_reviews.py"
    )
    spec = importlib.util.spec_from_file_location(
        "owned_review_migration", path
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def migrate(conn, action):
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, action)()

    async with engine.begin() as conn:
        for action in ("upgrade", "downgrade", "upgrade"):
            await conn.run_sync(migrate, action)
        columns = await conn.run_sync(
            lambda sync: inspect(sync).get_columns(
                AgentDeviceScriptReview.__tablename__
            )
        )
        assert {col["name"] for col in columns} == set(
            AgentDeviceScriptReview.__table__.columns.keys()
        )

    async with sessions() as db:
        cases = service(db, owner)
        version = await cases.save(
            project, actor, agent, command(binding)
        )
        report = await cases.run(
            project,
            actor,
            agent,
            version.id,
            ScriptTrialControl(
                request_id=uuid4(),
                binding_id=binding.id,
                binding_revision=binding.revision,
            ),
        )
    scope = project, actor, agent, report.id
    review = ScriptReviewWrite(
        case_version_id=version.id,
        case_digest=version.case_digest,
        result_digest=report.result_digest,
        decision="approved",
    )
    async with sessions() as db:
        cases = service(db, owner)
        reviews = AgentDeviceScriptReviews(cases)
        assert (await reviews.read(*scope)).status == "unreviewed"
        assert (
            await reviews.check_run(*scope)
        ).status == "review_required"
        with pytest.raises(NotFoundError):
            await reviews.read(project, uuid4(), agent, report.id)
        with pytest.raises(ConflictError):
            await reviews.decide(
                *scope,
                review.model_copy(update={"case_digest": "f" * 64}),
            )
        await db.rollback()
        approved = await reviews.decide(*scope, review)
        assert approved.status == "approved"
        assert (
            approved.review is not None and approved.can_enable is False
        )
        assert approved.review.expires_at <= report.expires_at
        assert PRIVATE not in approved.model_dump_json()
        assert await reviews.decide(*scope, review) == approved
        ready = await reviews.check_run(*scope)
        assert ready.status == "ready" and ready.remaining_runs == 2
        assert ready.review == approved and ready.can_enable is False
        assert ready.valid_until <= approved.review.expires_at
        assert ready.valid_until <= ready.checked_at + timedelta(
            seconds=30
        )
        assert owner.posts == 1

        owner.connection = "e" * 32
        assert (
            await reviews.check_run(*scope)
        ).status == "device_changed"
        owner.connection = "c" * 32
        owner.posts = 3
        empty = await reviews.check_run(*scope)
        assert (
            empty.status == "budget_exhausted"
            and empty.remaining_runs == 0
        )
        owner.posts = 1
        original_budget = owner.read_script_budget

        async def stale_budget(*args):
            value = await original_budget(*args)
            return value.model_copy(
                update={
                    "observed_at": datetime.now(
                        value.observed_at.tzinfo
                    )
                    - timedelta(seconds=31),
                }
            )

        with monkeypatch.context() as stale:
            stale.setattr(owner, "read_script_budget", stale_budget)
            assert (
                await reviews.check_run(*scope)
            ).status == "device_changed"

        async def unavailable_budget(*args):
            raise ExternalServiceError("device-control", "offline")

        with monkeypatch.context() as offline:
            offline.setattr(owner, "read_script_budget", unavailable_budget)
            assert (await reviews.check_run(*scope)).status == "unavailable"

        class LateClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(seconds=40)

        with monkeypatch.context() as elapsed:
            elapsed.setattr(agent_device_script_reviews, "datetime", LateClock)
            assert (
                await reviews.check_run(*scope)
            ).status == "device_changed"

        async def revoke_while_reading(*args):
            value = await original_budget(*args)
            async with sessions() as concurrent:
                await AgentDeviceScriptReviews(
                    service(concurrent, owner)
                ).revoke(*scope, approved.review.id)
            return value

        with monkeypatch.context() as race:
            race.setattr(
                owner, "read_script_budget", revoke_while_reading
            )
            revoked_check = await reviews.check_run(*scope)
            assert revoked_check.status == "review_required"
            assert revoked_check.review.status == "revoked"
        # Check expiry and version changes on another approved report.
        report = await cases.run(
            project,
            actor,
            agent,
            version.id,
            ScriptTrialControl(
                request_id=uuid4(),
                binding_id=binding.id,
                binding_revision=binding.revision,
            ),
        )
        scope = project, actor, agent, report.id
        review = review.model_copy(
            update={"result_digest": report.result_digest}
        )
        approved = await reviews.decide(*scope, review)
    async with sessions() as db:
        cases = service(db, owner)
        reviews = AgentDeviceScriptReviews(cases)
        assert await reviews.read(*scope) == approved

        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return approved.review.expires_at + timedelta(seconds=1)

        with monkeypatch.context() as clock:
            clock.setattr(
                agent_device_script_reviews, "datetime", ExpiredClock
            )
            assert (await reviews.read(*scope)).status == "expired"
            assert (
                await reviews.check_run(*scope)
            ).status == "review_required"
        next_version = await cases.save(
            project,
            actor,
            agent,
            command(
                binding,
                case_id=version.case_id,
                expected_revision=1,
                expected_fields={"ready": False, "label": PRIVATE},
            ),
        )
        assert (await reviews.read(*scope)).status == "changed"
        revoked = await reviews.revoke(*scope, approved.review.id)
        assert revoked.status == "revoked"
        assert revoked.review.decision == "approved"
        assert (
            await reviews.revoke(*scope, approved.review.id) == revoked
        )
        assert await reviews.decide(*scope, review) == revoked
        assert (
            await reviews.check_run(*scope)
        ).status == "review_required"
        report = await cases.run(
            project,
            actor,
            agent,
            next_version.id,
            ScriptTrialControl(
                request_id=uuid4(),
                binding_id=binding.id,
                binding_revision=binding.revision,
            ),
        )
        assert report.status == "mismatched"
        mismatch = ScriptReviewWrite(
            case_version_id=next_version.id,
            case_digest=next_version.case_digest,
            result_digest=report.result_digest,
            decision="approved",
        )
        scope = project, actor, agent, report.id
        with pytest.raises(ConflictError, match="不一致"):
            await reviews.decide(*scope, mismatch)
        await db.rollback()
        rejected = await reviews.decide(
            *scope, mismatch.model_copy(update={"decision": "rejected"})
        )
        assert rejected.status == "rejected"
    assert owner.posts == 3
    with pytest.raises(RuntimeError, match="history exists"):
        async with engine.begin() as conn:
            await conn.run_sync(migrate, "downgrade")
