"""Sender classification contracts for WuKongIM session statistics."""

from app.api.v1.endpoints.wukongim_webhook import _classify_message_sender


def test_classifies_widget_visitor_message() -> None:
    assert (
        _classify_message_sender(
            {
                "from_uid": "visitor-id-vtr",
                "client_msg_no": "widget-message",
            }
        )
        == "visitor"
    )


def test_classifies_ai_message_before_staff_uid() -> None:
    assert (
        _classify_message_sender(
            {
                "from_uid": "staff-id-staff",
                "client_msg_no": "ai_123",
            }
        )
        == "ai"
    )


def test_classifies_staff_and_system_messages() -> None:
    assert (
        _classify_message_sender(
            {
                "from_uid": "staff-id-staff",
                "client_msg_no": "staff-message",
            }
        )
        == "staff"
    )
    assert (
        _classify_message_sender(
            {
                "from_uid": "system",
                "client_msg_no": "handoff-message",
            }
        )
        == "system"
    )
