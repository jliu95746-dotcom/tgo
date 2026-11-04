from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.wecom_native import NativeContextEvent
from app.services.wecom_native_context import (
    apply_native_state,
    conversation_context,
    message_identity,
    preserve_native_config,
)


def test_saving_credentials_from_an_older_form_preserves_routing_and_allows_explicit_disable():
    existing = {
        "native_handoff_enabled": True,
        "native_capacity": 20,
        "app_secret": "old-test-value",
    }
    changed = preserve_native_config(existing, {"app_secret": "new-test-value"})
    assert changed["native_handoff_enabled"] is True
    assert changed["native_capacity"] == 20
    assert changed["app_secret"] == "new-test-value"
    assert (
        preserve_native_config(existing, {"native_handoff_enabled": False})[
            "native_handoff_enabled"
        ]
        is False
    )


def test_human_takeover_blocks_auto_and_completion_restores_ai_for_next_consultation():
    visitor = SimpleNamespace(
        service_mode="auto", ai_disabled=False, custom_attributes={}
    )
    apply_native_state(visitor, 3, "staff-a")
    assert visitor.service_mode == "manual"
    assert visitor.ai_disabled is True
    apply_native_state(visitor, 4, "", previous_state=3)
    assert visitor.service_mode == "auto"
    assert visitor.ai_disabled is False


def test_a_normal_customer_does_not_reset_an_explicit_mode():
    visitor = SimpleNamespace(
        service_mode="assist", ai_disabled=True, custom_attributes={}
    )
    apply_native_state(visitor, 0, "")
    assert visitor.service_mode == "assist"


def test_closed_session_does_not_require_a_cached_previous_state_to_leave_manual():
    visitor = SimpleNamespace(
        service_mode="manual", ai_disabled=True, service_status="closed"
    )
    apply_native_state(visitor, 4, "", previous_state=None)
    assert visitor.service_mode == "auto"
    assert visitor.ai_disabled is False


def test_closed_session_reopened_by_customer_does_not_route_back_to_staff():
    visitor = SimpleNamespace(
        service_mode="manual", ai_disabled=True, service_status="closed"
    )
    apply_native_state(visitor, 0, "", previous_state=4)
    assert visitor.service_mode == "auto"
    assert visitor.ai_disabled is False


def test_pending_closed_session_requests_release_instead_of_a_new_assignment(
    monkeypatch,
):
    from unittest.mock import Mock

    from app.services import wecom_native_context

    platform = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        config={"native_enabled_at": "2026-10-19T00:00:00+00:00"},
    )
    rows = [
        SimpleNamespace(
            id=uuid4(), platform_open_id="closed-customer", service_status="closed"
        ),
        SimpleNamespace(
            id=uuid4(), platform_open_id="active-customer", service_status="active"
        ),
    ]
    query = Mock()
    query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = rows
    monkeypatch.setattr(wecom_native_context, "native_platform", lambda *_: platform)
    result = wecom_native_context.pending_native_visitors(
        SimpleNamespace(query=Mock(return_value=query)), uuid4()
    )
    assert result.visitors[0].release_requested is True
    assert result.visitors[1].release_requested is False


def test_replayed_callbacks_keep_the_same_receipt_and_tenants_are_separated():
    project_a, project_b = uuid4(), uuid4()
    assert message_identity(project_a, "source-1") == message_identity(
        project_a, "source-1"
    )
    assert message_identity(project_a, "source-1") != message_identity(
        project_b, "source-1"
    )


def test_factual_ai_input_contains_human_context_with_explicit_roles():
    result = conversation_context(
        "已有规则",
        [
            SimpleNamespace(role="customer", content="我要黑色"),
            SimpleNamespace(role="staff", content="已经记下黑色，等待你确认"),
        ],
    )
    assert '"role": "staff"' in result
    assert "已经记下黑色" in result
    assert "不能替代" in result


def test_context_payload_rejects_unknown_roles_and_unbounded_content():
    with pytest.raises(ValueError):
        NativeContextEvent(
            platform_id=uuid4(),
            external_userid="customer",
            event_id="one",
            kind="system",
            service_state=3,
        )
    with pytest.raises(ValueError):
        NativeContextEvent(
            platform_id=uuid4(),
            external_userid="customer",
            event_id="one",
            kind="staff",
            service_state=3,
            content="x" * 9000,
        )
