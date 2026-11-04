from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from app.services.wecom_app_notifications import pending_assist_notifications
from app.services.wecom_native_context import preserve_native_config


def test_disabled_and_missing_enablement_do_not_replay_old_messages():
    db = Mock()
    for config in [{}, {"app_notification_enabled": True}]:
        assert pending_assist_notifications(db, SimpleNamespace(config=config)) == []
    db.query.assert_not_called()


def test_only_unread_effective_assist_visitors_generate_notifications(monkeypatch):
    from app.services import chat_service

    monkeypatch.setattr(
        chat_service, "resolve_service_mode", lambda p, v: v.service_mode
    )
    platform = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        config={
            "app_notification_enabled": True,
            "app_notification_enabled_at": datetime.now(UTC).isoformat(),
        },
    )
    rows = [
        SimpleNamespace(id=uuid4(), service_mode=mode, last_client_msg_no=mode)
        for mode in ["auto", "manual", "assist"]
    ]
    query = Mock()
    query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = rows
    db = Mock()
    db.query.return_value = query
    result = pending_assist_notifications(db, platform)
    assert [item.event_id for item in result] == ["assist"]
    filters = " ".join(str(arg) for arg in query.filter.call_args.args)
    for column in [
        "project_id",
        "platform_id",
        "deleted_at",
        "service_status",
        "is_last_message_from_visitor",
        "last_message_at",
    ]:
        assert column in filters


def test_channel_credential_forms_preserve_notification_configuration():
    config = {
        "app_notification_enabled": True,
        "app_notification_users": ["employee"],
        "app_notification_base_url": "https://support.example.com",
        "app_notification_enabled_at": "2026-10-19T00:00:00+00:00",
    }
    merged = preserve_native_config(config, {"corp_id": "new-corp"})
    assert all(merged[key] == value for key, value in config.items())
