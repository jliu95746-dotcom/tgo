"""Readiness can be shown before billing is enabled without exposing secrets."""

from unittest.mock import Mock

from pydantic import SecretStr

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.services import commercial_readiness


def test_readiness_reports_missing_configuration_without_enabling_billing(monkeypatch):
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    monkeypatch.setattr(
        settings, "SAAS_INTERNAL_TOKEN", SecretStr("synthetic-credential-only-for-test")
    )
    monkeypatch.setattr(
        settings, "WECHAT_PAY_REFUND_NOTIFY_URL", "http://insecure.invalid"
    )
    monkeypatch.setattr(
        commercial_readiness, "require_mail_configuration", lambda: None
    )
    monkeypatch.setattr(
        commercial_readiness,
        "WeChatPayClient",
        Mock(side_effect=TGOAPIException("synthetic-secret-error")),
    )
    result = commercial_readiness.readiness()
    states = {item.code: item.configured for item in result.checks}
    assert states == {
        "email": True,
        "wechat": False,
        "refund_callback": False,
        "internal_auth": True,
    }
    assert not result.billing_enabled and not result.purchases_enabled
    assert "synthetic" not in result.model_dump_json()
