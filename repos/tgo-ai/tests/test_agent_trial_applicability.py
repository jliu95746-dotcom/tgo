"""Revision evidence is not business approval, and reads preserve history."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.schemas.agent_trials import AgentTrialReport, TrialKnowledgeRevision, TrialKnowledgeSnapshot
from app.services.agent_trial_dependencies import AgentTrialDependencies, compare_snapshots
from tests.test_agent_knowledge_trial import employee


def snapshot(channel="web", revision="a" * 64, **changes):
    return TrialKnowledgeSnapshot(
        channel=channel, binding_mode="explicit", captured_at=datetime.now(UTC),
        collections=[TrialKnowledgeRevision(collection_id=uuid4(), resource_revision=revision)],
        verification="consistent",
    ).model_copy(update=changes)


def report(saved):
    return AgentTrialReport(
        id=uuid4(), agent_id=uuid4(), request_key=uuid4(), status="completed",
        channel="web", question="材质？", answer="牛皮", created_at=datetime.now(UTC),
        configuration_fingerprint="b" * 64, knowledge_snapshot=saved,
    )


@pytest.mark.asyncio
async def test_running_report_is_not_checked_before_snapshot_has_been_read():
    owner = AgentTrialDependencies(None)
    owner.current = AsyncMock()
    value = report(None).model_copy(update={"status": "running"})
    checked = await owner.check(value, uuid4())
    assert checked.status == "not_checked" and checked.issues == ["trial_not_completed"]
    owner.current.assert_not_awaited()


@pytest.mark.asyncio
async def test_unchanged_knowledge_is_current_but_never_approved():
    saved = snapshot()
    owner = AgentTrialDependencies(None)
    owner.current = AsyncMock(return_value=("b" * 64, saved))
    value = report(saved)
    checked = await owner.check(value, uuid4())
    assert checked.status == "current" and checked.checked_at
    assert value.assessment == "not_reviewed" and value.can_enable is False
    assert value.answer == "牛皮" and value.knowledge_snapshot == saved


@pytest.mark.asyncio
@pytest.mark.parametrize("change,code", [
    ("configuration", "configuration_changed"), ("knowledge", "knowledge_changed"),
    ("collection_removed", "knowledge_changed"), ("channel", "knowledge_changed"),
])
async def test_changed_dependencies_require_retest(change, code):
    saved = snapshot()
    current, fingerprint = saved, "b" * 64
    if change == "configuration":
        fingerprint = "c" * 64
    elif change == "knowledge":
        current = saved.model_copy(update={"collections": [
            saved.collections[0].model_copy(update={"resource_revision": "d" * 64}),
        ]})
    elif change == "collection_removed":
        current = saved.model_copy(update={"collections": []})
    else:
        current = saved.model_copy(update={"channel": "internal"})
    owner = AgentTrialDependencies(None)
    owner.current = AsyncMock(return_value=(fingerprint, current))
    checked = await owner.check(report(saved), uuid4())
    assert checked.status == "changed" and code in checked.issues


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [None, snapshot(verification="pending"),
    snapshot(verification="unknown"), snapshot(verification="changed")])
async def test_unrecorded_or_unconfirmed_snapshots_never_become_current(saved):
    owner = AgentTrialDependencies(None)
    owner.current = AsyncMock()
    checked = await owner.check(report(saved), uuid4())
    assert checked.status in {"not_recorded", "unknown", "changed"}
    owner.current.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_owner_revision_is_unknown_and_config_change_is_still_detected():
    saved = snapshot()
    current = saved.model_copy(update={"issues": ["missing_revision"], "collections": [
        saved.collections[0].model_copy(update={"resource_revision": None}),
    ]})
    owner = AgentTrialDependencies(None)
    owner.current = AsyncMock(return_value=("b" * 64, current))
    assert (await owner.check(report(saved), uuid4())).status == "unknown"
    owner.current.return_value = ("c" * 64, current)
    assert (await owner.check(report(saved), uuid4())).status == "changed"


@pytest.mark.asyncio
async def test_owner_failure_has_no_sensitive_details():
    owner = AgentTrialDependencies(None)
    owner.current = AsyncMock(side_effect=RuntimeError("Authorization: private-provider"))
    checked = await owner.check(report(snapshot()), uuid4())
    assert checked.status == "unknown"
    assert "private-provider" not in checked.model_dump_json()


def test_snapshot_comparison_ignores_read_time_and_order_but_requires_revisions():
    first = snapshot()
    second = first.model_copy(update={"captured_at": datetime.now(UTC)})
    assert compare_snapshots(first, second) == "consistent"
    assert compare_snapshots(first, second.model_copy(update={"issues": ["missing_revision"]})) == "unknown"
    changed = second.model_copy(update={"binding_mode": "project_default"})
    assert compare_snapshots(first, changed) == "changed"


@pytest.mark.asyncio
async def test_capture_uses_owned_channel_revisions_and_hides_names(monkeypatch):
    from app.schemas.knowledge_availability import AgentKnowledgeAvailability, CollectionAvailability
    from app.services import agent_trial_dependencies as module
    agent = employee()
    available = AgentKnowledgeAvailability(
        project_id=agent.project_id, agent_id=agent.id, channel="web", binding_mode="explicit",
        collections=[CollectionAvailability(id=uuid4(), name="敏感资料名", eligible_chunk_count=1,
            total_chunk_count=1, resource_revision="a" * 64)],
    )
    lookup = AsyncMock(return_value=available)
    monkeypatch.setattr(module, "agent_knowledge_availability", lookup)
    captured = await module.read_knowledge_snapshot(agent, "web")
    assert captured.collections[0].resource_revision == "a" * 64
    assert "敏感资料名" not in captured.model_dump_json()
    available.project_id = uuid4()
    assert (await module.read_knowledge_snapshot(agent, "web")).issues == ["knowledge_unavailable"]
