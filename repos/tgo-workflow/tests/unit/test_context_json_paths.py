"""Nested business fields preserve existing flat-key priority."""

from types import SimpleNamespace

import pytest

from app.engine.context import ExecutionContext


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("event.data.order.status", "已发货"),
        ("event.data.items.0.sku", "LV-T01"),
        ("event.data.items.1.sku", "LV-S04"),
        ("event.data.quantity", 0),
        ("event.data.available", False),
        ("event.data.absent", None),
        ("event.data.items.2.sku", None),
        ("event.data.items.-1.sku", None),
        ("event.data.items.__class__", None),
        ("event.data.items.０.sku", None),
        ("event.data.order.status.upper", None),
        ("event.data..order", None),
        ("event.data.order.", None),
    ],
)
def test_nested_json_object_and_array_fields(path, expected):
    context = ExecutionContext({"event.data": {
        "order": {"status": "已发货"},
        "items": [{"sku": "LV-T01"}, {"sku": "LV-S04"}],
        "quantity": 0,
        "available": False,
    }})
    assert context.get_variable(path) == expected


def test_flat_keys_and_null_override_nested_values():
    context = ExecutionContext({
        "event.data": {"order": {"status": "已发货", "note": "nested"}},
        "event.data.order.status": "explicit flat value",
        "event.data.order.note": None,
    })
    assert context.get_variable("event.data.order.status") == (
        "explicit flat value"
    )
    assert context.get_variable("event.data.order.note") is None


def test_longest_flat_prefix_controls_nested_resolution():
    context = ExecutionContext({
        "event.data": {"order": {"status": "shorter namespace"}},
        "event.data.order": {"status": "closest namespace"},
    })
    assert context.get_variable("event.data.order.status") == (
        "closest namespace"
    )


def test_templates_and_recursive_values_resolve_nested_fields():
    context = ExecutionContext({
        "event.data": {"order": {"status": "已发货"}},
    })
    template = "状态：{{ event.data.order.status }}"
    assert context.resolve_template(template) == "状态：已发货"
    value = {"text": ["{{event.data.order.status}}"]}
    assert context.resolve_variables(value) == {
        "text": ["已发货"],
    }
    missing = "{{event.data.missing}}"
    assert context.resolve_template(missing) == missing


def test_nested_lookup_never_reads_python_attributes():
    context = ExecutionContext({
        "event.data": SimpleNamespace(private="not JSON"),
    })
    assert context.get_variable("event.data.private") is None
    assert context.get_variable("event.data.__dict__.private") is None


def test_unbounded_fallback_paths_are_left_unresolved():
    context = ExecutionContext({"event.data": {"value": "bounded"}})
    assert context.get_variable("event.data." + "x" * 1024) is None
    assert context.get_variable("event.data." + ".".join(["x"] * 64)) is None
