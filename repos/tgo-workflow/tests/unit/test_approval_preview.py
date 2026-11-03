"""Approval previews bind an actual run's scalar inputs to a visible decision."""

import pytest

from app.services.approval_preview import (
    approval_preview_digest,
    render_approval_preview,
)


def test_preview_renders_current_order_without_treating_customer_text_as_template():
    summary = render_approval_preview(
        "核对订单 {{input.order_number}}：{{input.reason}}",
        {"input.order_number": "LY-123", "input.reason": "肩带损坏"},
    )
    assert summary == "核对订单 LY-123：肩带损坏"
    assert approval_preview_digest(
        "approval-a", "run-a", summary
    ) != approval_preview_digest(
        "approval-a",
        "run-a",
        "核对订单 LY-124：肩带损坏",
    )


@pytest.mark.parametrize(
    "template, data",
    [
        ("订单 {{input.order_number}}", {}),
        ("订单 {{input.order_number}}", {"input.order_number": ["LY-123"]}),
        ("订单 {{input.order_number}}", {"input.order_number": "{{private.token}}"}),
        ("订单 {{input.order_number", {"input.order_number": "LY-123"}),
    ],
)
def test_preview_rejects_missing_or_unsafe_values(template, data):
    with pytest.raises(ValueError):
        render_approval_preview(template, data)


def test_preview_rejects_resolved_text_over_500_characters():
    with pytest.raises(ValueError):
        render_approval_preview("{{input.reason}}", {"input.reason": "x" * 501})


def test_preview_resolves_only_explicit_scalar_fields_in_source_json():
    values = {"source.body": {"question": "请人工核对", "items": [{"sku": "SKU-1"}]}}
    assert (
        render_approval_preview(
            "{{source.body.question}} / {{source.body.items.0.sku}}", values
        )
        == "请人工核对 / SKU-1"
    )
    for path in (
        "source.body",
        "source.body.missing",
        "source.body.items.-1",
        "source.body.__class__",
    ):
        with pytest.raises(ValueError):
            render_approval_preview("{{" + path + "}}", values)
    values["source.body.question"] = "精确的平铺值"
    assert render_approval_preview("{{source.body.question}}", values) == "精确的平铺值"
