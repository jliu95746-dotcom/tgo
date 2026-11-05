"""An awaited model response must still match the current configuration."""

import asyncio
from datetime import datetime, timedelta
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import AIProvider, Visitor, VisitorAssignmentRule
from app.services import transfer_service
from app.services.ai_client import ai_client

pytestmark = pytest.mark.asyncio
PRIVATE = "synthetic-private-expired-dispatch-response"


@pytest.fixture
def pending_assignment(monkeypatch):
    project = uuid4()
    version = datetime(2026, 1, 1)
    provider = AIProvider(
        id=uuid4(),
        project_id=project,
        provider="openai",
        name="Synthetic",
        api_key="synthetic-unused-key",
        default_model="fixture-model",
        is_active=True,
        updated_at=version,
    )
    rule = VisitorAssignmentRule(
        id=uuid4(),
        project_id=project,
        ai_provider_id=provider.id,
        model="fixture-model",
        llm_assignment_enabled=True,
        prompt="Synthetic policy",
        updated_at=version,
    )
    candidates = [
        transfer_service.StaffCandidate(
            uuid4(), "busy", None, None, "online", 3
        ),
        transfer_service.StaffCandidate(
            uuid4(), "free", None, None, "online", 0
        ),
    ]
    state = {AIProvider: provider, VisitorAssignmentRule: rule}
    queries = {model: MagicMock() for model in state}
    for model, query in queries.items():
        query.filter.return_value = query
        query.populate_existing.return_value = query
        query.with_for_update.return_value = query
        query.first.side_effect = lambda model=model: state[model]
    db = MagicMock(spec=Session)
    db.query.side_effect = lambda model: queries[model]
    complete = AsyncMock()
    monkeypatch.setattr(ai_client, "chat_completions", complete)

    async def run():
        return await transfer_service._llm_assign_staff(
            db,
            project,
            Visitor(id=uuid4(), project_id=project),
            "Need assistance",
            candidates,
            rule,
        )

    content = json.dumps(
        {
            "selected_staff_id": str(candidates[0].id),
            "reasoning": PRIVATE,
        }
    )
    return run, complete, state, queries, rule, provider, candidates, content


@pytest.mark.parametrize(
    "change",
    [
        None,
        "provider_disabled",
        "provider_deleted",
        "provider_version",
        "rule_disabled",
        "rule_deleted",
        "rule_provider",
        "rule_model",
        "rule_prompt",
        "rule_version",
    ],
)
async def test_configuration_is_rechecked_after_the_await(
    pending_assignment, caplog, change
):
    (
        run,
        complete,
        state,
        queries,
        rule,
        provider,
        candidates,
        content,
    ) = pending_assignment

    async def response(**_kwargs):
        # Configuration writes must remain possible during the external wait.
        for query in queries.values():
            query.with_for_update.assert_not_called()
        if change in {"provider_disabled", "provider_deleted"}:
            state[AIProvider] = None
        elif change == "provider_version":
            provider.updated_at += timedelta(seconds=1)
        elif change == "rule_disabled":
            rule.llm_assignment_enabled = False
        elif change == "rule_deleted":
            state[VisitorAssignmentRule] = None
        elif change == "rule_provider":
            rule.ai_provider_id = uuid4()
        elif change == "rule_model":
            rule.model = "different-model"
        elif change == "rule_prompt":
            rule.prompt = "Changed policy"
        elif change == "rule_version":
            rule.updated_at += timedelta(seconds=1)
        return {"choices": [{"message": {"content": content}}]}

    complete.side_effect = response
    with caplog.at_level("INFO"):
        result = await run()
    complete.assert_awaited_once()
    if change is None:
        assert result["selected_staff_id"] == candidates[0].id
        assert result["reasoning"] == PRIVATE
        assert not result.get("refresh_candidates", False)
        for query in queries.values():
            query.with_for_update.assert_called_once_with(read=True)
    else:
        assert result["selected_staff_id"] == candidates[1].id
        assert result["refresh_candidates"] is True
        assert result["llm_response"] is None
        assert PRIVATE not in result["reasoning"]
        assert result["model_used"] == "fixture-model"
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


async def test_failure_also_rechecks_current_configuration(pending_assignment):
    (
        run,
        complete,
        state,
        _queries,
        _rule,
        _provider,
        _candidates,
        _content,
    ) = pending_assignment

    async def unavailable(**_kwargs):
        state[AIProvider] = None
        raise RuntimeError(PRIVATE)

    complete.side_effect = unavailable
    result = await run()
    assert result["refresh_candidates"] is True
    assert PRIVATE not in result["reasoning"]


async def test_cancellation_never_becomes_a_fallback(pending_assignment):
    (
        run,
        complete,
        _state,
        queries,
        _rule,
        _provider,
        _candidates,
        _content,
    ) = pending_assignment
    complete.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await run()
    for query in queries.values():
        query.with_for_update.assert_not_called()


@pytest.mark.parametrize("unavailable", ["disabled", "removed"])
async def test_current_rule_is_checked_before_the_request(
    pending_assignment, unavailable
):
    (
        run,
        complete,
        state,
        _queries,
        rule,
        _provider,
        candidates,
        _content,
    ) = pending_assignment
    if unavailable == "disabled":
        rule.llm_assignment_enabled = False
    else:
        state[VisitorAssignmentRule] = None
    result = await run()
    complete.assert_not_awaited()
    assert result["selected_staff_id"] == candidates[1].id
    assert result["refresh_candidates"] is True
    assert result["model_used"] is None and result["prompt_used"] is None
