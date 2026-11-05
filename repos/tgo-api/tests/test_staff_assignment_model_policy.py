"""Dispatcher model choice follows current project provider configuration."""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import AIProvider, Visitor, VisitorAssignmentRule
from app.services import transfer_service
from app.services.ai_client import ai_client

pytestmark = pytest.mark.asyncio


@pytest.fixture
def model_case(monkeypatch):
    project = uuid4()
    provider = AIProvider(
        id=uuid4(),
        project_id=project,
        provider="openai",
        name="Synthetic dispatcher",
        api_key="synthetic-unused-key",
        default_model="provider-default",
        is_active=True,
    )
    rule = VisitorAssignmentRule(
        id=uuid4(),
        project_id=project,
        ai_provider_id=provider.id,
        model=None,
        llm_assignment_enabled=True,
    )
    candidates = [
        transfer_service.StaffCandidate(
            uuid4(), "specialist", None, None, "online", 3
        ),
        transfer_service.StaffCandidate(
            uuid4(), "available", None, None, "online", 0
        ),
    ]
    complete = AsyncMock(
        return_value={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"selected_staff_id": str(candidates[0].id)}
                        )
                    }
                }
            ]
        }
    )
    monkeypatch.setattr(ai_client, "chat_completions", complete)
    db = MagicMock(spec=Session)
    query = MagicMock()
    rule_query = MagicMock()
    for target, row in [(query, provider), (rule_query, rule)]:
        target.filter.return_value = target
        target.populate_existing.return_value = target
        target.with_for_update.return_value = target
        target.first.return_value = row
    db.query.side_effect = lambda model: (
        query if model is AIProvider else rule_query
    )

    async def run():
        return await transfer_service._llm_assign_staff(
            db,
            project,
            Visitor(id=uuid4(), project_id=project),
            "Need assistance",
            candidates,
            rule,
        )

    return run, complete, rule, provider, query, db, candidates


@pytest.mark.parametrize(
    "explicit, default, expected",
    [
        (None, "provider-default", "provider-default"),
        ("", "provider-default", "provider-default"),
        ("  ", "provider-default", "provider-default"),
        ("explicit-model", "provider-default", "explicit-model"),
        (" explicit-model ", None, "explicit-model"),
        (None, " provider-default ", "provider-default"),
    ],
)
async def test_model_precedence_matches_configuration(
    model_case, explicit, default, expected
):
    run, complete, rule, provider, _query, _db, candidates = model_case
    rule.model = explicit
    provider.default_model = default
    result = await run()
    assert result["selected_staff_id"] == candidates[0].id
    assert complete.await_args.kwargs["model"] == expected
    assert complete.await_args.kwargs["provider_id"] == str(provider.id)
    assert result["model_used"] == expected


@pytest.mark.parametrize("default", [None, "", "  "])
async def test_missing_models_fall_back_without_inventing_a_model(
    model_case, default
):
    run, complete, _rule, provider, _query, _db, candidates = model_case
    provider.default_model = default
    result = await run()
    assert result["selected_staff_id"] == candidates[1].id
    assert "load balancing" in result["reasoning"].lower()
    assert result["model_used"] is None and result["prompt_used"] is None
    complete.assert_not_awaited()


async def test_unavailable_provider_never_receives_an_ai_request(model_case):
    run, complete, rule, _provider, query, _db, candidates = model_case
    rule.model = "explicit-model"
    rows = query.filter.return_value.populate_existing.return_value
    rows.first.return_value = None
    result = await run()
    assert result["selected_staff_id"] == candidates[1].id
    assert result["model_used"] is None and result["prompt_used"] is None
    complete.assert_not_awaited()


async def test_provider_query_enforces_current_project_and_lifecycle(
    model_case,
):
    run, _complete, rule, provider, query, db, _candidates = model_case
    await run()
    assert [call.args[0] for call in db.query.call_args_list].count(
        AIProvider
    ) == 2
    clauses = query.filter.call_args.args
    expected = (
        AIProvider.id == rule.ai_provider_id,
        AIProvider.project_id == rule.project_id,
        AIProvider.is_active.is_(True),
        AIProvider.deleted_at.is_(None),
    )
    assert len(clauses) == len(expected)
    assert all(
        actual.compare(wanted) for actual, wanted in zip(clauses, expected)
    )
    assert query.populate_existing.call_count == 2
    assert provider.project_id == rule.project_id


async def test_foreign_rule_cannot_borrow_the_current_projects_provider(
    model_case,
):
    run, complete, rule, _provider, _query, db, candidates = model_case
    rule.project_id = uuid4()
    result = await run()
    assert result["selected_staff_id"] == candidates[1].id
    db.query.assert_not_called()
    complete.assert_not_awaited()
