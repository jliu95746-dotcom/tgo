"""Deployment switches must agree across validation, dispatch and routing."""

import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.services.node_readiness import (
    node_configuration_errors,
    workflow_capabilities,
)
from app.services.timer_state import timer_capability_enabled
from app.services.workflow_schedules import execution_available


def test_timer_execution_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TIMER_EXECUTION_ENABLED", raising=False)
    configuration = Settings(_env_file=None)
    assert configuration.TIMER_EXECUTION_ENABLED is False
    assert configuration.WORKFLOW_QUEUE_NAME == "workflow"


@pytest.mark.parametrize("enabled", [False, True])
def test_deployment_switch_controls_all_timer_entry_checks(
    monkeypatch, enabled
):
    monkeypatch.setattr(settings, "TIMER_EXECUTION_ENABLED", enabled)
    timer = next(
        node for node in workflow_capabilities().nodes if node.type == "timer"
    )
    assert timer.available is enabled
    assert timer_capability_enabled() is enabled
    assert execution_available() is enabled
    errors = node_configuration_errors(
        [
            {
                "id": "clock",
                "type": "timer",
                "data": {
                    "cron_expression": "0 9 * * *",
                    "timezone": "Asia/Shanghai",
                },
            }
        ]
    )
    assert any("not supported" in error for error in errors) is not enabled


@pytest.mark.parametrize("queue", ["", "a/b", "a b", "x" * 129])
def test_queue_names_reject_unsafe_or_unbounded_values(queue):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, WORKFLOW_QUEUE_NAME=queue)


def test_every_periodic_task_and_execution_uses_the_configured_queue(
    monkeypatch,
):
    monkeypatch.setattr(settings, "WORKFLOW_QUEUE_NAME", "timer-test-isolated")
    source = Path(__file__).parents[2] / "celery_app/celery.py"
    spec = importlib.util.spec_from_file_location(
        "isolated_celery_config", source
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = module.celery_app.conf
    assert config.task_default_queue == "timer-test-isolated"
    assert config.task_routes["celery_app.tasks.*"]["queue"] == (
        "timer-test-isolated"
    )
    assert all(
        entry["options"]["queue"] == "timer-test-isolated"
        for entry in config.beat_schedule.values()
    )
    module.celery_app.close()
