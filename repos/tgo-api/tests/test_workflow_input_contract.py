"""Input requirements survive the authenticated gateway's schema serialization."""

import pytest
from pydantic import ValidationError

from app.schemas.ai_workflows import InputVariable, WorkflowCreate


def test_required_field_is_retained_and_legacy_missing_is_optional():
    assert InputVariable(name="order_number", type="string", required=True).model_dump()["required"] is True
    assert InputVariable(name="legacy", type="string").required is False


@pytest.mark.parametrize("value", ["false", "true", 0, 1])
def test_required_flag_cannot_be_coerced(value):
    with pytest.raises(ValidationError):
        InputVariable(name="order_number", type="string", required=value)


def test_gateway_preserves_declared_input_constraints_in_workflow_payload():
    request = WorkflowCreate.model_validate({
        "name": "订单查询", "edges": [], "nodes": [{
            "id": "input", "type": "input", "position": {"x": 0, "y": 0},
            "data": {"type": "input", "label": "订单号", "reference_key": "input",
                     "input_variables": [{
                         "name": "order_no", "type": "string",
                         "required": True,
                         "min_length": 4, "max_length": 64,
                         "allowed_values": ["L001", "L002"],
                     }]},
        }],
    })
    variable = request.model_dump()["nodes"][0]["data"]["input_variables"][0]
    assert variable["min_length"] == 4
    assert variable["max_length"] == 64
    assert variable["allowed_values"] == ["L001", "L002"]


def test_gateway_preserves_business_choices_without_coercion():
    variable = InputVariable(name="reason", type="string", allowed_values=["退货", "换货"])
    assert variable.model_dump()["allowed_values"] == ["退货", "换货"]


@pytest.mark.parametrize("choices", [[], ["退货", "退货"], [" 退货"], ["x" * 101]])
def test_gateway_rejects_invalid_business_choices(choices):
    with pytest.raises(ValidationError):
        InputVariable(name="reason", type="string", allowed_values=choices)


@pytest.mark.parametrize("constraints", [
    {"min_length": 5, "max_length": 4},
    {"minimum": 0},
    {"max_length": -1},
])
def test_gateway_rejects_invalid_text_constraint_declarations(constraints):
    with pytest.raises(ValidationError):
        InputVariable(name="order_no", type="string", **constraints)
