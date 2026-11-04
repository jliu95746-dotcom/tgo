"""Execution values are flat typed business data and bind a fixed input digest."""

import pytest
from pydantic import ValidationError

from app.schemas.device_script_execution import (
    ScriptExecuteBody,
    ScriptReceiptQuery,
    ScriptWireReceipt,
    script_input_digest,
    validate_script_values,
)
from app.schemas.device_script_policy import ScriptParameter


def fields():
    return [
        ScriptParameter(
            name="count", kind="integer", required=True, minimum=0, maximum=100
        ),
        ScriptParameter(name="enabled", kind="boolean", required=True),
        ScriptParameter(
            name="label", kind="string", required=False, max_length=40
        ),
    ]


@pytest.mark.parametrize(
    "values",
    [
        {"count": True, "enabled": False},
        {"count": 1.0, "enabled": False},
        {"count": 1, "enabled": "false"},
        {"count": 101, "enabled": False},
        {"count": 1, "enabled": False, "label": "x\n"},
        {"count": 1, "enabled": False, "script_path": "private.py"},
        {"count": 1},
    ],
)
def test_values_cannot_choose_code_or_change_reviewed_types(values):
    with pytest.raises(ValueError):
        validate_script_values(fields(), values)


def test_digest_is_order_independent_and_optional_omission_is_preserved():
    values = dict(count=12, enabled=False, label="女包<&> 'quote' \\")
    validated = validate_script_values(fields(), values)
    assert script_input_digest("a" * 64, validated) == (
        "5f41350924e7bb44b73eec27a14db455" "5f4435dd247db84b091f27b497260163"
    )
    assert script_input_digest("a" * 64, validated) == script_input_digest(
        "a" * 64, dict(reversed(list(values.items())))
    )
    assert script_input_digest("a" * 64, validated) != script_input_digest(
        "b" * 64, validated
    )
    assert "label" not in validate_script_values(
        fields(), dict(count=12, enabled=False)
    )


def test_wire_receipt_requires_complete_identity_and_never_partial_fields():
    query = dict(
        protocol_version=1,
        session_id="d" * 32,
        execution_id="33333333-3333-4333-8333-333333333333",
        policy_digest="a" * 64,
        parameters_digest="b" * 64,
        context_digest="c" * 64,
    )
    assert ScriptReceiptQuery.model_validate(query).session_id == "d" * 32
    with pytest.raises(ValidationError):
        ScriptReceiptQuery.model_validate({**query, "command": "anything"})
    reply = {key: value for key, value in query.items() if key != "session_id"}
    with pytest.raises(ValidationError):
        ScriptWireReceipt.model_validate(
            {
                **reply,
                "status": "unknown",
                "failure": "unconfirmed",
                "replayed": True,
                "fields": {"private": "partial"},
            }
        )
    with pytest.raises(ValidationError):
        ScriptExecuteBody.model_validate(
            {"parameters": {"nested": {"code": "anything"}}}
        )
