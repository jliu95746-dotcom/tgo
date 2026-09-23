from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1.endpoints import chat
from app.models import ChannelMember, Visitor
from app.schemas.chat import StaffSendPlatformMessageRequest


class FakeQuery:
    def __init__(self, result: object) -> None:
        self.result = result

    def options(self, *_: object) -> "FakeQuery":
        return self

    def filter(self, *_: object) -> "FakeQuery":
        return self

    def first(self) -> object:
        return self.result


class FakeDb:
    def __init__(self, membership: object, visitor: object) -> None:
        self.membership = membership
        self.visitor = visitor

    def query(self, model: object) -> FakeQuery:
        if model is ChannelMember:
            return FakeQuery(self.membership)
        if model is Visitor:
            return FakeQuery(self.visitor)
        raise AssertionError(f"Unexpected model query: {model}")


def build_context(*, synthetic_visitor: bool) -> tuple[object, object, FakeDb, str]:
    staff_id = uuid4()
    project_id = uuid4()
    visitor_id = uuid4()
    channel_id = f"{visitor_id}-vtr"
    platform = SimpleNamespace(
        id=uuid4(),
        project_id=project_id,
        type="wecom",
        deleted_at=None,
        is_active=True,
        api_key="platform-key",
        agent_id=None,
    )
    visitor = SimpleNamespace(
        id=visitor_id,
        project_id=project_id,
        platform_open_id=(channel_id if synthetic_visitor else "external-user-id"),
        platform=platform,
    )
    staff = SimpleNamespace(id=staff_id, project_id=project_id)
    membership = SimpleNamespace(member_id=staff_id)
    return staff, platform, FakeDb(membership, visitor), channel_id


def build_request(channel_id: str) -> StaffSendPlatformMessageRequest:
    return StaffSendPlatformMessageRequest(
        channel_id=channel_id,
        channel_type=251,
        payload={"type": 1, "content": "你好"},
        client_msg_no="staff-message-id",
    )


@pytest.mark.asyncio
async def test_simulator_visitor_skips_external_platform_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staff, _, db, channel_id = build_context(synthetic_visitor=True)

    class ForbiddenExternalClient:
        def __init__(self, *_: object, **__: object) -> None:
            raise AssertionError("Simulator message must not call the external platform")

    monkeypatch.setattr(chat.httpx, "AsyncClient", ForbiddenExternalClient)

    response = await chat.staff_send_platform_message(
        build_request(channel_id),
        db,
        staff,
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "ok": True,
        "delivery": "wukongim",
        "message": "Simulator visitor uses internal chat delivery",
    }


@pytest.mark.asyncio
async def test_real_wecom_visitor_still_uses_external_platform_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staff, _, db, channel_id = build_context(synthetic_visitor=False)
    outbound_requests: list[dict[str, object]] = []

    class FakeResponse:
        content = b'{"ok":true}'
        status_code = 200
        headers = {"content-type": "application/json"}

    class FakeExternalClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def __aenter__(self) -> "FakeExternalClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> FakeResponse:
            outbound_requests.append({"url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(chat.httpx, "AsyncClient", FakeExternalClient)

    response = await chat.staff_send_platform_message(
        build_request(channel_id),
        db,
        staff,
    )

    assert response.status_code == 200
    assert len(outbound_requests) == 1
    assert outbound_requests[0]["url"].endswith("/v1/messages/send")
