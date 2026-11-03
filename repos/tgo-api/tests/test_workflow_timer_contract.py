"""The gateway must preserve the schedule zone in both graph directions."""

import pytest
from pydantic import ValidationError

from app.schemas.ai_workflows import TimerNodeData, WorkflowDefinition


@pytest.mark.parametrize("zone", ["Asia/Shanghai", "America/New_York", "UTC"])
def test_timer_zone_survives_definition_read_and_forwarding(zone: str) -> None:
    definition = WorkflowDefinition.model_validate(
        {
            "nodes": [
                {
                    "id": "timer",
                    "type": "timer",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "type": "timer",
                        "label": "定时整理",
                        "reference_key": "timer",
                        "cron_expression": "0 9 * * *",
                        "timezone": zone,
                    },
                }
            ],
            "edges": [],
        }
    )
    serialized = definition.model_dump(by_alias=True)
    assert serialized["nodes"][0]["data"]["timezone"] == zone
    assert serialized["nodes"][0]["data"]["cron_expression"] == "0 9 * * *"


def test_old_timer_definition_defaults_to_utc() -> None:
    data = TimerNodeData(
        label="定时整理",
        reference_key="timer",
        cron_expression="0 9 * * *",
    )
    assert data.timezone == "UTC"


@pytest.mark.parametrize("zone", [True, 123, "", "x" * 101])
def test_gateway_rejects_wrong_zone_type_or_size(zone: object) -> None:
    with pytest.raises(ValidationError):
        TimerNodeData.model_validate(
            {
                "label": "定时整理",
                "reference_key": "timer",
                "cron_expression": "0 9 * * *",
                "timezone": zone,
            }
        )
