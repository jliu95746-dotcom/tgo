from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.entities import NormalizedMessage
from app.domain.services.dispatcher import select_adapter_for_target
from app.domain.services.wecom_credentials import resolve_wecom_kf_secret


def test_kf_secret_takes_precedence_over_legacy_app_secret() -> None:
    assert resolve_wecom_kf_secret(" kf-secret ", "app-secret") == "kf-secret"
    assert resolve_wecom_kf_secret("", " app-secret ") == "app-secret"
    assert resolve_wecom_kf_secret(None, None) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_from_colleague", "expected_secret"),
    [(False, "kf-secret"), (True, "app-secret")],
)
async def test_wecom_adapter_uses_secret_for_its_message_channel(
    is_from_colleague: bool, expected_secret: str,
) -> None:
    platform = SimpleNamespace(type="wecom", config={
        "corp_id": "corp-id", "agent_id": "1000002",
        "app_secret": "app-secret", "kf_secret": "kf-secret",
    })
    message = NormalizedMessage(
        source="wecom", from_uid="recipient", content="hello",
        platform_api_key="platform-key", platform_type="wecom",
        platform_id="00000000-0000-0000-0000-000000000001",
        extra={"wecom": {
            "is_from_colleague": is_from_colleague,
            "open_kfid": "wk-test", "external_userid": "recipient",
        }},
    )

    adapter = await select_adapter_for_target(message, platform)

    assert adapter.app_secret == expected_secret
