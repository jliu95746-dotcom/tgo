"""Human case judgments require current evidence and preserve their history."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import AgentTrialReview, AgentTestReport, BaseModel
from app.schemas.agent_trials import AgentTrialReport, TrialApplicability, TrialKnowledgeSnapshot, TrialKnowledgeRevision
from app.schemas.agent_trial_reviews import KnowledgeReviewCase, KnowledgeReviewCommand, KnowledgeReviewChecks
from app.services.agent_trial_reviews import AgentTrialReviewService


@pytest_asyncio.fixture
async def setup():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(lambda db: BaseModel.metadata.create_all(
            db, tables=[AgentTestReport.__table__, AgentTrialReview.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        project, actor, agent, report_id = [uuid4() for _ in range(4)]
        case = KnowledgeReviewCase(id="knowledge.v1.1", template_id="knowledge", template_version=1,
            question="这款包是什么材质？", expected_result="grounded_answer")
        snapshot = TrialKnowledgeSnapshot(channel="web", binding_mode="explicit", captured_at=datetime.now(UTC),
            collections=[TrialKnowledgeRevision(collection_id=uuid4(), resource_revision="a"*64)], verification="consistent")
        db.add(AgentTestReport(id=report_id, project_id=project, created_by=actor, agent_id=agent,
            request_key=uuid4(), request_digest="b"*64, status="completed", channel="web", question=case.question,
            answer="牛皮材质", sources=[{"collection_id": str(snapshot.collections[0].collection_id),
                "document_id": str(uuid4()), "file_id": None, "content_digest": "a"*64, "relevance_score": 0.9}],
            configuration_fingerprint="c"*64, knowledge_status="matched", knowledge_snapshot=snapshot.model_dump(mode="json"),
            completed_at=datetime.now(UTC), expires_at=datetime.now(UTC)+timedelta(minutes=1)))
        await db.commit()
        row = await db.get(AgentTestReport, report_id)
        report = AgentTrialReport.model_validate(row).model_copy(update={"applicability": TrialApplicability(status="current")})
        trials = Mock(get=AsyncMock(return_value=report))
        trials.dependencies.check = AsyncMock(return_value=TrialApplicability(status="current"))
        service = AgentTrialReviewService(db, trials)
        command = KnowledgeReviewCommand(request_key=uuid4(), case_id=case.id, case=case, decision="passed",
            checks=KnowledgeReviewChecks(answer_correct=True, evidence_checked=True, no_unverified_claims=True, expected_behavior=True))
        yield db, service, project, actor, agent, report, command, trials
    await engine.dispose()


@pytest.mark.asyncio
async def test_review_is_idempotent_scoped_and_never_activates(setup):
    db, service, project, actor, agent, report, command, _ = setup
    first = await service.review(project, actor, agent, report.id, command)
    retry = await service.review(project, actor, agent, report.id, command)
    assert first.id == retry.id and first.decision == "passed" and first.effective
    assert first.can_enable is False and first.reviewed_by == actor
    assert len((await db.scalars(select(AgentTrialReview))).all()) == 1
    with pytest.raises(HTTPException) as failure:
        await service.review(project, actor, agent, report.id, command.model_copy(update={"note": "另一份评审"}))
    assert failure.value.status_code == 409


@pytest.mark.asyncio
async def test_catalog_change_keeps_retry_receipt_but_invalidates_old_pass(setup):
    db, service, project, actor, agent, report, command, _ = setup
    first = await service.review(project, actor, agent, report.id, command)
    changed = command.case.model_copy(update={"id": "kc.new.knowledge.1", "question": "企业提供哪些服务？"})
    retry_command = KnowledgeReviewCommand(**command.model_dump(exclude={"current_cases"}), current_cases=[changed])
    retry = await service.review(project, actor, agent, report.id, retry_command)
    assert retry.id == first.id and retry.case_status == "changed" and not retry.effective
    assert len((await db.scalars(select(AgentTrialReview))).all()) == 1


@pytest.mark.asyncio
async def test_retired_case_cannot_create_a_new_judgment(setup):
    db, service, project, actor, agent, report, command, _ = setup
    retired = KnowledgeReviewCommand(**command.model_dump(exclude={"current_cases"}), current_cases=[])
    with pytest.raises(HTTPException) as failure:
        await service.review(project, actor, agent, report.id, retired)
    assert failure.value.status_code == 409
    assert not (await db.scalars(select(AgentTrialReview))).all()


@pytest.mark.asyncio
async def test_question_version_requires_a_trial_started_after_configuration(setup):
    _, service, project, actor, agent, report, command, _ = setup
    updated = command.case.model_copy(update={"valid_from": datetime.now(UTC) + timedelta(seconds=1)})
    request = command.model_copy(update={"case": updated, "current_cases": [updated]})
    with pytest.raises(HTTPException) as failure:
        await service.review(project, actor, agent, report.id, request)
    assert failure.value.status_code == 409


def test_legacy_case_digest_is_unchanged():
    import hashlib
    import json
    from app.services.agent_trial_reviews import case_digest

    legacy = {"id": "knowledge.v1.1", "template_id": "knowledge", "template_version": 1,
              "criteria_version": 1, "question": "这款包是什么材质？", "expected_result": "grounded_answer"}
    expected = hashlib.sha256(json.dumps(legacy, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    assert case_digest(KnowledgeReviewCase.model_validate(legacy)) == expected


@pytest.mark.asyncio
async def test_original_request_still_resolves_after_the_visible_history_limit(setup):
    db, service, project, actor, agent, report, command, _ = setup
    first = await service.review(project, actor, agent, report.id, command)
    original = await db.get(AgentTrialReview, first.id)
    fields = {column.name: getattr(original, column.name) for column in AgentTrialReview.__table__.columns
              if column.name not in {"id", "revision", "request_key", "request_digest", "decision"}}
    db.add_all([AgentTrialReview(**fields, id=uuid4(), revision=revision, request_key=uuid4(),
        request_digest="f"*64, decision="rejected") for revision in range(2, 103)])
    await db.commit()
    state = await service.state(project, actor, agent, report.id, [command.case])
    assert len(state.reviews) == 100 and all(row.id != first.id for row in state.reviews)
    retry = await service.review(project, actor, agent, report.id, command)
    assert retry.id == first.id and retry.superseded and not retry.effective


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["changed", "unknown", "no_sources", "wrong_question", "unchecked", "failed"])
async def test_invalid_evidence_or_incomplete_confirmation_cannot_pass(setup, change):
    _, service, project, actor, agent, report, command, trials = setup
    if change in {"changed", "unknown"}:
        trials.get.return_value = report.model_copy(update={"applicability": TrialApplicability(status=change)})
        trials.dependencies.check.return_value = TrialApplicability(status=change)
    elif change == "no_sources":
        trials.get.return_value = report.model_copy(update={"sources": []})
    elif change == "wrong_question":
        command = command.model_copy(update={"case": command.case.model_copy(update={"question": "不同问题"})})
    elif change == "unchecked":
        command = command.model_copy(update={"checks": KnowledgeReviewChecks()})
    else:
        trials.get.return_value = report.model_copy(update={"status": "failed"})
    with pytest.raises(HTTPException):
        await service.review(project, actor, agent, report.id, command)


@pytest.mark.asyncio
async def test_changed_dependencies_keep_human_history_but_remove_effectiveness(setup):
    _, service, project, actor, agent, report, command, trials = setup
    first = await service.review(project, actor, agent, report.id, command)
    trials.get.return_value = report.model_copy(update={"applicability": TrialApplicability(status="changed")})
    state = await service.state(project, actor, agent, report.id, [command.case])
    assert state.reviews[0].decision == "passed" and not state.reviews[0].effective
    assert not state.can_review and first.id == state.reviews[0].id
    assert state.can_enable is False


@pytest.mark.asyncio
async def test_later_rejection_supersedes_prior_pass_without_deleting_history(setup):
    _, service, project, actor, agent, report, command, _ = setup
    first = await service.review(project, actor, agent, report.id, command)
    rejection = command.model_copy(update={"request_key": uuid4(), "decision": "rejected", "note": "需补齐资料",
                                           "expected_latest_review_id": first.id})
    await service.review(project, actor, agent, report.id, rejection)
    state = await service.state(project, actor, agent, report.id, [command.case])
    assert len(state.reviews) == 2 and not any(row.effective for row in state.reviews)
    assert [row.revision for row in state.reviews] == [2, 1]
    assert state.reviews[1].superseded and state.reviews[1].decision == "passed"


@pytest.mark.asyncio
async def test_updated_case_definition_invalidates_old_review(setup):
    _, service, project, actor, agent, report, command, _ = setup
    await service.review(project, actor, agent, report.id, command)
    state = await service.state(project, actor, agent, report.id, [command.case.model_copy(update={"expected_result": "safe_refusal"})])
    assert state.reviews[0].case_status == "changed" and not state.reviews[0].effective


@pytest.mark.asyncio
async def test_artifact_change_and_old_test_cannot_reuse_human_approval(setup):
    _, service, project, actor, agent, report, command, trials = setup
    await service.review(project, actor, agent, report.id, command)
    trials.get.return_value = report.model_copy(update={"answer": "变成另一份答案"})
    assert not (await service.state(project, actor, agent, report.id, [command.case])).reviews[0].effective
    trials.get.return_value = report.model_copy(update={"completed_at": datetime.now(UTC)-timedelta(days=8)})
    state = await service.state(project, actor, agent, report.id, [command.case])
    assert not state.can_review and not state.reviews[0].effective


@pytest.mark.asyncio
async def test_no_match_can_only_pass_safe_refusal_case_after_current_dependency_check(setup):
    _, service, project, actor, agent, report, command, trials = setup
    report = report.model_copy(update={"status": "blocked", "knowledge_status": "no_match", "sources": [],
        "answer": "当前资料无法确认库存。", "error_code": "knowledge_not_verified", "applicability": TrialApplicability(status="not_checked")})
    trials.get.return_value = report
    safe_case = command.case.model_copy(update={"expected_result": "safe_refusal"})
    result = await service.review(project, actor, agent, report.id, command.model_copy(update={"case": safe_case}))
    assert result.effective and result.can_enable is False
    assert trials.dependencies.check.await_args.kwargs["allow_safe_refusal"] is True
    with pytest.raises(HTTPException):
        await service.review(project, actor, agent, report.id, command.model_copy(update={"request_key": uuid4()}))


@pytest.mark.asyncio
async def test_review_notes_are_redacted_and_owner_failure_cannot_approve(setup):
    _, service, project, actor, agent, report, command, trials = setup
    response = await service.review(project, actor, agent, report.id, command.model_copy(update={"note": "邮箱 test@example.com"}))
    assert "test@example.com" not in response.model_dump_json()
    trials.get.side_effect = HTTPException(404, "未找到试聊报告")
    with pytest.raises(HTTPException) as failure:
        await service.state(uuid4(), actor, agent, report.id, [command.case])
    assert failure.value.status_code == 404
