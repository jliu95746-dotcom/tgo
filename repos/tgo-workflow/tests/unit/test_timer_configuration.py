"""Timer draft validation must not prematurely advertise execution."""

import pytest
from pydantic import ValidationError

from app.schemas.node import TimerNodeData
from app.services.node_readiness import (
    node_configuration_errors,
    workflow_capabilities,
)


def test_legacy_timer_configuration_has_an_explicit_utc_default() -> None:
    timer = TimerNodeData.model_validate(
        {
            "label": "定时整理",
            "reference_key": "timer",
            "cron_expression": " 0   9 * * * ",
        }
    )
    assert timer.timezone == "UTC"
    assert timer.cron_expression == "0 9 * * *"


@pytest.mark.parametrize(
    ("expression", "zone"),
    [
        ("60 * * * *", "UTC"),
        ("0 0 30 2 *", "UTC"),
        ("0 9 * * *", "Unknown/PrivateTenant"),
    ],
)
def test_schema_rejects_bad_rule_calendar_and_zone(
    expression: str, zone: str
) -> None:
    with pytest.raises(ValidationError):
        TimerNodeData.model_validate(
            {
                "label": "定时整理",
                "reference_key": "timer",
                "cron_expression": expression,
                "timezone": zone,
            }
        )


@pytest.mark.parametrize(
    "data",
    [
        {"cron_expression": "60 * * * *"},
        {"cron_expression": "0 0 30 2 *"},
        {"cron_expression": "0 9 * * *", "timezone": "Unknown/PrivateTenant"},
        {"cron_expression": "0 9 * * *", "timezone": True},
        {"cron_expression": 123},
        {},
    ],
)
def test_draft_validation_reports_invalid_schedule_before_enablement(
    data: dict[str, object]
) -> None:
    errors = node_configuration_errors(
        [
            {
                "id": "timer",
                "type": "timer",
                "data": data,
            }
        ]
    )
    assert any("定时" in error or "时区" in error for error in errors)
    assert any("not supported" in error for error in errors)


def test_valid_schedule_still_cannot_publish_or_claim_execution_support() -> (
    None
):
    errors = node_configuration_errors(
        [
            {
                "id": "timer",
                "type": "timer",
                "data": {
                    "cron_expression": "0 9 * * 1-5",
                    "timezone": "Asia/Shanghai",
                },
            }
        ]
    )
    assert len(errors) == 1
    assert "not supported" in errors[0]
    capability = next(
        item for item in workflow_capabilities().nodes if item.type == "timer"
    )
    assert capability.available is False
