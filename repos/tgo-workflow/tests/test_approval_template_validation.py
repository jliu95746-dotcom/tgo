"""Approval previews should fail at publication when their variables are wrong."""

from uuid import uuid4

from app.services.validation_service import ValidationService


def definition(summary: str) -> dict[str, object]:
    return {
        "nodes": [
            {"id": "start", "type": "input", "data": {
                "reference_key": "input",
                "input_variables": [
                    {"name": "order_number", "type": "string", "required": True},
                ],
            }},
            {"id": "approval", "type": "approval", "data": {
                "reference_key": "approval",
                "approver_staff_id": str(uuid4()),
                "summary": summary,
            }},
            {"id": "answer", "type": "answer", "data": {
                "reference_key": "answer", "output_type": "template",
                "output_template": "已由人工核对",
            }},
        ],
        "edges": [
            {"source": "start", "target": "approval"},
            {"source": "approval", "target": "answer"},
        ],
    }


def test_approval_preview_accepts_declared_input():
    assert ValidationService.validate_workflow(
        definition("核对订单 {{input.order_number}}"),
    ) == []


def test_approval_preview_rejects_undeclared_or_unknown_variable():
    for summary in (
        "订单 {{input.order_num}}",
        "订单 {{missing.number}}",
        "订单 {{answer.result}}",
    ):
        errors = ValidationService.validate_workflow(definition(summary))
        assert any("审批预览" in error for error in errors)


def test_approval_preview_rejects_broken_placeholders():
    for summary in ("订单 {{input.order_number}", "订单 {{input}}"):
        errors = ValidationService.validate_workflow(definition(summary))
        assert any("审批预览" in error for error in errors)


def test_legacy_input_without_declarations_keeps_runtime_validation():
    workflow = definition("核对订单 {{input.legacy_order}}")
    workflow["nodes"][0]["data"]["input_variables"] = []
    assert ValidationService.validate_workflow(workflow) == []
