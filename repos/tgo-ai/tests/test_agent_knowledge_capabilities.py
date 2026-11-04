"""Coverage requires every current business and safety case, never activation."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Agent, AgentTestReport, AgentTrialReview, BaseModel
from app.schemas.agent_knowledge_capabilities import KnowledgeCapabilityQuery
from app.schemas.agent_trial_reviews import (
    KnowledgeReviewCase,
    KnowledgeReviewChecks,
    KnowledgeReviewCommand,
)
from app.schemas.agent_trials import (
    AgentTrialReport,
    TrialApplicability,
    TrialKnowledgeRevision,
    TrialKnowledgeSnapshot,
)
from app.services.agent_knowledge_capabilities import AgentKnowledgeCapabilityService
from app.services.agent_trial_reviews import AgentTrialReviewService


@pytest_asyncio.fixture
async def fixture(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'coverage.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        project, actor, agent = uuid4(), uuid4(), uuid4()
        db.add(
            Agent(
                id=agent,
                project_id=project,
                name="知识测试员工",
                model="test",
                is_active=False,
            )
        )
        await db.commit()
        snapshot = TrialKnowledgeSnapshot(
            channel="web",
            binding_mode="explicit",
            captured_at=datetime.now(UTC),
            verification="consistent",
            collections=[
                TrialKnowledgeRevision(
                    collection_id=uuid4(), resource_revision="a" * 64
                )
            ],
        )
        cases = [
            KnowledgeReviewCase(
                id=f"knowledge.v1.{index + 1}",
                template_id="knowledge",
                template_version=1,
                question=f"测试问题{index}",
                expected_result="safe_refusal" if index == 2 else "grounded_answer",
            )
            for index in range(3)
        ]
        trials = Mock()
        trials.dependencies.current = AsyncMock(return_value=("c" * 64, snapshot))
        trials.dependencies.check = AsyncMock(
            return_value=TrialApplicability(status="current")
        )

        async def get(p, s, a, report_id):
            row = await db.get(AgentTestReport, report_id)
            assert (row.project_id, row.created_by, row.agent_id) == (p, s, a)
            return AgentTrialReport.model_validate(row).model_copy(
                update={"applicability": TrialApplicability(status="current")}
            )

        trials.get = get
        reviews = AgentTrialReviewService(db, trials)
        service = AgentKnowledgeCapabilityService(reviews)

        async def make(
            case,
            *,
            passed=True,
            status="completed",
            channel="web",
            creator=actor,
            owner=project,
            created=None,
            safe_no_match=False,
        ):
            row = AgentTestReport(
                id=uuid4(),
                project_id=owner,
                created_by=creator,
                agent_id=agent,
                request_key=uuid4(),
                request_digest="b" * 64,
                status="blocked" if safe_no_match else status,
                channel=channel,
                question=case.question,
                answer="当前资料无法确认库存"
                if case.expected_result == "safe_refusal"
                else "有依据的测试回答",
                sources=[]
                if safe_no_match
                else [
                    {
                        "collection_id": str(snapshot.collections[0].collection_id),
                        "document_id": str(uuid4()),
                        "content_digest": "a" * 64,
                        "relevance_score": 0.9,
                    }
                ],
                configuration_fingerprint="c" * 64,
                knowledge_status="no_match" if safe_no_match else "matched",
                knowledge_snapshot=snapshot.model_dump(mode="json"),
                completed_at=datetime.now(UTC),
                created_at=created or datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                error_code="knowledge_not_verified" if safe_no_match else None,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            receipt = None
            if passed and status == "completed":
                receipt = await reviews.review(
                    owner,
                    creator,
                    agent,
                    row.id,
                    KnowledgeReviewCommand(
                        request_key=uuid4(),
                        case_id=case.id,
                        case=case,
                        decision="passed",
                        checks=KnowledgeReviewChecks(
                            answer_correct=True,
                            evidence_checked=True,
                            no_unverified_claims=True,
                            expected_behavior=True,
                        ),
                    ),
                )
            return row, receipt

        yield db, service, reviews, project, actor, agent, cases, trials, make, snapshot
    await engine.dispose()


async def read(f):
    _, service, _, project, actor, agent, cases, *_ = f
    return await service.read(
        project, actor, agent, KnowledgeCapabilityQuery(cases=cases)
    )


@pytest.mark.asyncio
async def test_no_tests_and_single_answer_are_not_complete(fixture):
    value = await read(fixture)
    assert (
        value.capabilities[0].status == "not_tested"
        and not value.can_enable
        and not value.enabled
    )
    await fixture[8](fixture[6][0])
    value = await read(fixture)
    assert (
        value.capabilities[0].answer_cases_passed == 1
        and value.capabilities[0].status != "passed"
    )
    fixture[7].dependencies.current.assert_awaited()


@pytest.mark.asyncio
async def test_all_required_answers_and_safety_pass_but_do_not_enable(fixture):
    for case in fixture[6]:
        await fixture[8](case)
    value = await read(fixture)
    coverage = value.capabilities[0]
    assert (
        coverage.status == "passed"
        and coverage.answer_cases_passed == 2
        and coverage.safety_cases_passed == 1
    )
    assert coverage.valid_until and not value.can_enable and not value.enabled
    assert not (await fixture[0].get(Agent, fixture[5])).is_active


@pytest.mark.asyncio
async def test_safe_refusal_alone_never_means_can_answer(fixture):
    case = fixture[6][2]
    await fixture[8](case, safe_no_match=True)
    result = await fixture[1].read(
        fixture[3], fixture[4], fixture[5], KnowledgeCapabilityQuery(cases=[case])
    )
    assert result.capabilities[0].safety_cases_passed == 1
    assert (
        result.capabilities[0].answer_cases_passed == 0
        and result.capabilities[0].status != "passed"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", "running", "completed"])
async def test_new_trial_never_falls_back_to_previous_approval(fixture, status):
    await fixture[8](fixture[6][0], created=datetime.now(UTC) - timedelta(hours=1))
    await fixture[8](fixture[6][0], passed=False, status=status)
    value = await read(fixture)
    case = value.capabilities[0].cases[0]
    assert case.status == (
        "failed"
        if status in {"failed", "cancelled"}
        else "in_progress"
        if status == "running"
        else "awaiting_review"
    )
    assert value.capabilities[0].answer_cases_passed == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["project", "actor", "channel"])
async def test_foreign_report_is_not_coverage(fixture, scope):
    kwargs = (
        {"owner": uuid4()}
        if scope == "project"
        else {"creator": uuid4()}
        if scope == "actor"
        else {"channel": "wecom_kf"}
    )
    await fixture[8](fixture[6][0], **kwargs)
    assert (await read(fixture)).capabilities[0].cases[0].status == "not_tested"
    with pytest.raises(HTTPException) as failure:
        await fixture[1].read(
            uuid4(), fixture[4], fixture[5], KnowledgeCapabilityQuery(cases=fixture[6])
        )
    assert failure.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["configuration", "knowledge", "expired", "criteria", "answer"]
)
async def test_invalid_old_evidence_loses_coverage(fixture, change):
    row, receipt = await fixture[8](fixture[6][0])
    if change in {"configuration", "knowledge"}:
        fixture[7].dependencies.check.return_value = TrialApplicability(
            status="changed"
        )
    elif change == "expired":
        (await fixture[0].get(AgentTrialReview, receipt.id)).valid_until = datetime.now(
            UTC
        ) - timedelta(seconds=1)
    elif change == "criteria":
        fixture[6][0] = fixture[6][0].model_copy(update={"template_version": 2})
        fixture[6][1] = fixture[6][1].model_copy(update={"template_version": 2})
        fixture[6][2] = fixture[6][2].model_copy(update={"template_version": 2})
    else:
        row.answer = "未经确认的另一份回答"
    await fixture[0].commit()
    assert (await read(fixture)).capabilities[0].cases[0].status == "needs_retest"


@pytest.mark.asyncio
async def test_same_timestamp_is_ambiguous_and_never_guessed(fixture):
    moment = datetime.now(UTC)
    await fixture[8](fixture[6][0], created=moment)
    await fixture[8](fixture[6][0], passed=False, status="failed", created=moment)
    assert (await read(fixture)).capabilities[0].cases[0].status == "needs_retest"


@pytest.mark.asyncio
async def test_unavailable_owner_retains_history_without_passed_coverage(fixture):
    await fixture[8](fixture[6][0])
    fixture[7].dependencies.current.side_effect = RuntimeError(
        "do not expose private connection details"
    )
    fixture[7].dependencies.check.return_value = TrialApplicability(status="unknown")
    value = await read(fixture)
    assert (
        value.knowledge_status == "unknown"
        and value.capabilities[0].status == "unavailable"
    )
    assert (
        value.capabilities[0].cases[0].report_id
        and "private connection" not in value.model_dump_json()
    )


@pytest.mark.asyncio
async def test_later_rejection_replaces_positive_case(fixture):
    row, receipt = await fixture[8](fixture[6][0])
    await fixture[2].review(
        fixture[3],
        fixture[4],
        fixture[5],
        row.id,
        KnowledgeReviewCommand(
            request_key=uuid4(),
            case_id=fixture[6][0].id,
            case=fixture[6][0],
            decision="rejected",
            expected_latest_review_id=receipt.id,
        ),
    )
    assert (await read(fixture)).capabilities[0].cases[0].status == "failed"


def test_duplicate_cases_cannot_create_incomplete_coverage():
    case = KnowledgeReviewCase(
        id="same",
        template_id="knowledge",
        template_version=1,
        question="问题",
        expected_result="grounded_answer",
    )
    with pytest.raises(ValidationError):
        KnowledgeCapabilityQuery(cases=[case, case])
