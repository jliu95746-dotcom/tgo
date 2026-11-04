"""Beat pulses come from its real tick, with failures isolated from other jobs."""

from unittest.mock import Mock
from celery import Celery
from celery.beat import PersistentScheduler
from app.config import settings
from celery_app import timer_scheduler


def test_disabled_timer_does_not_emit_or_shorten_existing_tick(monkeypatch):
    monkeypatch.setattr(settings, "TIMER_EXECUTION_ENABLED", False)
    publish = Mock()
    monkeypatch.setattr(timer_scheduler, "publish_timer_probe", publish)
    monkeypatch.setattr(PersistentScheduler, "tick", lambda self, **kwargs: 60)
    scheduler = timer_scheduler.TimerHealthScheduler(app=Celery("probe-test"), lazy=True)
    assert scheduler.tick() == 60
    publish.assert_not_called()


def test_tick_pulses_are_bounded_and_failure_does_not_stop_old_jobs(monkeypatch):
    monkeypatch.setattr(settings, "TIMER_EXECUTION_ENABLED", True)
    clock = [100.0]
    monkeypatch.setattr(timer_scheduler.time, "monotonic", lambda: clock[0])
    publish = Mock(side_effect=OSError("synthetic-private-url"))
    monkeypatch.setattr(timer_scheduler, "publish_timer_probe", publish)
    monkeypatch.setattr(PersistentScheduler, "tick", lambda self, **kwargs: 60)
    scheduler = timer_scheduler.TimerHealthScheduler(app=Celery("probe-test"), lazy=True)
    assert scheduler.tick() == 15
    assert scheduler.tick() == 15
    assert publish.call_count == 1
    clock[0] += 15
    scheduler.tick()
    assert publish.call_count == 2
