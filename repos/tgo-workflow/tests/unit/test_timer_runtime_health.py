"""Queue readiness is recent, scoped evidence, not a configured boolean."""

from uuid import uuid4
import pytest
from app.config import settings
from app.services.timer_probe import TimerProbe, assess_runtime, probe_scope
from app.services import timer_runtime_health


def record(role="beat", *, instance=None, at=100, ready=None):
    return TimerProbe(
        role=role, instance_id=instance or uuid4(), probe_id=uuid4(),
        observed_at=at, database_ready=ready,
    ).model_dump_json()


def test_recent_probe_and_database_receipt_are_required():
    instance = uuid4()
    result = assess_runtime(
        record(instance=instance),
        record("worker", instance=instance, ready=True), now=100,
    )
    assert result.ready and result.status == "ready"
    assert result.scheduler_recent and result.worker_recent
    assert "instance_id" not in result.model_dump_json()


@pytest.mark.parametrize("beat, worker, expected", [
    (None, None, "scheduler_missing"),
    ("broken", None, "invalid_probe"),
    (record(at=54), None, "scheduler_missing"),
    (record(at=200), None, "invalid_probe"),
])
def test_missing_stale_or_future_evidence_fails_closed(beat, worker, expected):
    result = assess_runtime(beat, worker, now=100)
    assert not result.ready and result.status == expected


def test_previous_scheduler_cannot_confirm_new_scheduler():
    result = assess_runtime(
        record(instance=uuid4()), record("worker", ready=True), now=100,
    )
    assert not result.ready and result.status == "worker_waiting"


def test_worker_database_failure_is_distinct_from_queue_timeout():
    instance = uuid4()
    result = assess_runtime(
        record(instance=instance),
        record("worker", instance=instance, ready=False), now=100,
    )
    assert result.worker_recent and not result.database_ready
    assert result.status == "database_unavailable"


def test_scope_separates_database_queue_and_private_authority(monkeypatch):
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("synthetic-health-key"))
    original = probe_scope()
    monkeypatch.setattr(settings, "WORKFLOW_QUEUE_NAME", "other-health-queue")
    assert probe_scope() != original
    monkeypatch.setattr(settings, "WORKFLOW_QUEUE_NAME", "workflow")
    monkeypatch.setattr(settings, "SECRET_KEY", SecretStr("other-test-key"))
    assert probe_scope() != original


@pytest.mark.asyncio
async def test_disabled_read_does_not_connect_to_redis(monkeypatch):
    monkeypatch.setattr(settings, "TIMER_EXECUTION_ENABLED", False)
    result = await timer_runtime_health.get_timer_runtime_state()
    assert not result.ready and result.status == "disabled"
