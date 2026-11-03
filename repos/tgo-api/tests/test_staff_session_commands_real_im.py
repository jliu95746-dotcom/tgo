"""Opt-in route-to-WuKongIM verification with synthetic SQLite staff records.

Run only against a local IM service. Random channels are removed in finally;
synthetic messages can remain in IM storage after channel deletion.
"""

import asyncio
import os
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.v1.endpoints import sessions as endpoints
from app.models import SessionCommandDelivery
from app.services.session_command_membership import deliver_channel_effects
from app.services.wukongim_client import WuKongIMClient, wukongim_client as im
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id
from tests.test_staff_session_commands import (  # noqa: F401
    bearer, real_session_api, send, session_api,
)
from tests.test_staff_visitor_read_access import staff_read_api  # noqa: F401
from tests.test_visitor_profile_access import profile_api  # noqa: F401
from tests.test_im_access import records  # noqa: F401


pytestmark = pytest.mark.skipif(
    os.getenv("TGO_REAL_IM_TEST") != "1",
    reason="Explicit local WuKongIM verification is required",
)


async def wait_conversation(uid: str, channel_id: str, present: bool) -> None:
    deadline = asyncio.get_running_loop().time() + 10
    while True:
        rows = await im.sync_conversations(uid=uid)
        found = any(
            row.channel_id == channel_id
            and row.channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE
            for row in rows
        )
        if found == present:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Synthetic conversation state did not converge")
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    ["close_by_id", "close_by_visitor", "transfer_by_id", "transfer_by_visitor"],
)
async def test_session_route_commits_and_applies_real_im_effects(
    real_session_api, monkeypatch, command,  # noqa: F811
):
    app, db, staff, visitors, target, original = real_session_api
    assert urlsplit(im.base_url).hostname in {"127.0.0.1", "localhost", "::1"}
    assert im.enabled
    for name in (
        "send_message", "remove_channel_subscribers", "delete_conversation",
        "add_channel_subscribers", "get_channel_last_message",
    ):
        method = getattr(WuKongIMClient, name)
        monkeypatch.setattr(im, name, method.__get__(im, WuKongIMClient))
    monkeypatch.setattr(endpoints, "deliver_channel_effects", deliver_channel_effects)

    channel_id = build_visitor_channel_id(visitors[0].id)
    old_uid = f"{staff[0].id}-staff"
    target_uid = f"{target.id}-staff"
    channel_created = False
    try:
        await im.create_channel(
            channel_id=channel_id,
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            subscribers=[old_uid, channel_id],
        )
        channel_created = True
        await im.send_message(
            from_uid=channel_id, channel_id=channel_id,
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            no_persist=False, payload={"type": 1, "content": "fixture"},
        )
        await wait_conversation(old_uid, channel_id, True)
        path = (
            f"/sessions/{original.id}/{command.split('_')[0]}"
            if command.endswith("by_id") else
            f"/sessions/visitor/{visitors[0].id}/{command.split('_')[0]}"
        )
        body = (
            {"target_staff_id": str(target.id)}
            if command.startswith("transfer") else None
        )
        operation_id = uuid4()
        result = await send(app, path, staff[0], body, operation_id)
        assert result.status_code == 200, result.text
        assert result.headers["x-session-operation-id"] == str(operation_id)
        assert result.headers["x-session-effects-status"] == "done"
        assert result.headers["x-session-notification-status"] == "sent"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            receipt = await client.get(
                f"/sessions/operations/{operation_id}", headers=bearer(staff[0]),
            )
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["effects_status"] == "done"
        assert receipt.json()["notification_status"] == "sent"
        delivery = db.get(SessionCommandDelivery, UUID(str(operation_id)))
        assert delivery is not None
        assert delivery.effects_attempts == 1 and delivery.attempts == 1
        notice = await im.get_message_by_client_msg_no(
            channel_id=channel_id,
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            client_msg_no=delivery.client_msg_no,
            raise_on_error=True,
        )
        assert notice is not None
        await wait_conversation(old_uid, channel_id, False)
        if command.startswith("transfer"):
            await wait_conversation(target_uid, channel_id, True)
        else:
            assert result.json()["status"] == "closed"
    finally:
        cleanup_errors = []
        if channel_created:
            for uid in (old_uid, target_uid, channel_id):
                try:
                    await im.delete_conversation(
                        uid=uid, channel_id=channel_id,
                        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                    )
                except Exception:
                    cleanup_errors.append("synthetic conversation")
            async with httpx.AsyncClient(
                base_url=im.base_url, timeout=10, trust_env=False,
            ) as client:
                try:
                    response = await client.post(
                        "/channel/delete",
                        json={"channel_id": channel_id,
                              "channel_type": CHANNEL_TYPE_CUSTOMER_SERVICE},
                    )
                    response.raise_for_status()
                except Exception:
                    cleanup_errors.append("synthetic channel")
        await im.aclose()
        assert not cleanup_errors, cleanup_errors
