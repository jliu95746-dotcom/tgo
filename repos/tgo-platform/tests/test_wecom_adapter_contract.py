from __future__ import annotations

import hashlib

import pytest

from app.domain.services.adapters import wecom as wecom_adapter


@pytest.mark.asyncio
async def test_kf_reply_uses_stable_outgoing_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_payloads: list[dict[str, object]] = []

    async def fake_access_token(*_: object, **__: object) -> str:
        return "access-token"

    async def fake_send(**kwargs: object) -> dict[str, object]:
        sent_payloads.append(kwargs)
        return {"errcode": 0}

    monkeypatch.setattr(wecom_adapter, "wecom_get_access_token", fake_access_token)
    monkeypatch.setattr(wecom_adapter, "wecom_kf_send_msg", fake_send)
    adapter = wecom_adapter.WeComAdapter(
        corp_id="corp-id",
        agent_id=None,
        app_secret="app-secret",
        to_user="external-user",
        is_from_colleague=False,
        open_kfid="wk-test",
        external_userid="external-user",
        source_message_id="customer-message",
    )

    await adapter.send_final({"text": "reply"})

    expected_digest = hashlib.sha256(b"customer-message").hexdigest()
    assert sent_payloads[0]["message_id"] == f"tgo_{expected_digest[:28]}"
    assert len(str(sent_payloads[0]["message_id"])) == 32


@pytest.mark.asyncio
async def test_kf_reply_truncates_at_a_valid_2048_byte_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_payloads: list[dict[str, object]] = []

    async def fake_access_token(*_: object, **__: object) -> str:
        return "access-token"

    async def fake_send(**kwargs: object) -> dict[str, object]:
        sent_payloads.append(kwargs)
        return {"errcode": 0}

    monkeypatch.setattr(wecom_adapter, "wecom_get_access_token", fake_access_token)
    monkeypatch.setattr(wecom_adapter, "wecom_kf_send_msg", fake_send)
    adapter = wecom_adapter.WeComAdapter(
        corp_id="corp-id",
        agent_id=None,
        app_secret="app-secret",
        to_user="external-user",
        is_from_colleague=False,
        open_kfid="wk-test",
        external_userid="external-user",
    )

    await adapter.send_final({"text": "汉" * 683})

    text = sent_payloads[0]["content"]["content"]
    assert isinstance(text, str)
    assert len(text.encode("utf-8")) <= 2048
    assert text
