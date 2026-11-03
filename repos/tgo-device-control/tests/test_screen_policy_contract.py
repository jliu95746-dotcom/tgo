"""The screen protocol intersects a fresh, opaque local grant; it is not publication."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.schemas.device_screen_policy import ScreenGrantStatus, ScreenPolicy
from app.services.device_screen_policy import (
    apply_screen_policy,
    screen_policy_digest,
    validate_local_screen_scope,
)
from app.services import device_screen_policy
from app.services import mcp_server


def grant():
    return ScreenGrantStatus(
        protocol_version=1,
        active=True,
        grant_id="a" * 32,
        width=640,
        height=480,
        dpi=120,
        allow_click=True,
        allow_text=False,
        click_regions=[{"x": 20, "y": 50, "width": 300, "height": 40}],
        max_actions=10,
        used_actions=2,
    )


def policy(**changes):
    values = dict(
        protocol_version=1,
        revision=1,
        grant_id="a" * 32,
        max_actions=5,
        allow_click=False,
        allow_text=False,
        click_regions=[],
        digest="0" * 64,
    )
    values.update(changes)
    result = ScreenPolicy.model_validate(values)
    return result.model_copy(update={"digest": screen_policy_digest(result)})


def test_contract_rejects_extra_window_identity_coercion_and_unbounded_values():
    for changes in (
        {"window_handle": 10},
        {"max_actions": True},
        {"allow_text": "false"},
        {"max_actions": 51},
        {"protocol_version": True},
        {"revision": 0},
    ):
        with pytest.raises(ValidationError):
            policy(**changes)


def test_server_scope_cannot_rebind_expand_or_replenish_used_actions():
    current = grant()
    assert validate_local_screen_scope(current, policy())
    for candidate in (
        policy(grant_id="b" * 32),
        policy(max_actions=11),
        policy(max_actions=2),
        policy(allow_text=True),
        policy(
            allow_click=True,
            click_regions=[{"x": 319, "y": 50, "width": 2, "height": 1}],
        ),
    ):
        assert not validate_local_screen_scope(current, candidate)


def test_canonical_vector_matches_go_and_inactive_scope_is_empty():
    assert (
        policy().digest
        == "3e1772db3e2809fb5dddd5b25fa4f4bf3051622c3059bdc707d506b40a1826d2"
    )
    assert not ScreenGrantStatus(protocol_version=1, active=False).active
    with pytest.raises(ValidationError):
        ScreenGrantStatus(protocol_version=1, active=False, grant_id="a" * 32)


@pytest.mark.asyncio
async def test_ack_is_bound_to_same_grant_revision_digest_and_connection(monkeypatch):
    desired = policy()
    connection = SimpleNamespace(
        agent_id="owned-device",
        desired_screen_policy=None,
        applied_screen_policy_digest=None,
        screen_grant=None,
        connection_instance_id="owned-current-connection",
        send_request=AsyncMock(
            side_effect=[
                grant().model_dump(),
                {
                    "protocol_version": 1,
                    "revision": 1,
                    "digest": desired.digest,
                    "grant_id": "a" * 32,
                },
            ]
        ),
    )
    monkeypatch.setattr(
        device_screen_policy.tcp_connection_manager,
        "get_connection",
        lambda _: connection,
    )
    assert await apply_screen_policy(connection, desired)
    assert connection.applied_screen_policy_digest == desired.digest
    assert connection.send_request.await_args_list[0].args[0] == "screen/status"
    assert connection.send_request.await_args_list[1].args[0] == "screen/policy/apply"
    connection.send_request.side_effect = [
        grant().model_dump(),
        {
            "protocol_version": 1,
            "revision": 1,
            "digest": desired.digest,
            "grant_id": "b" * 32,
        },
    ]
    assert not await apply_screen_policy(connection, desired)
    assert connection.applied_screen_policy_digest is None


@pytest.mark.asyncio
async def test_connection_replacement_during_ack_does_not_reuse_confirmation(
    monkeypatch,
):
    desired = policy()
    connection = SimpleNamespace(
        connection_instance_id="first",
        send_request=None,
        agent_id="owned-device",
        desired_screen_policy=None,
        applied_screen_policy_digest=None,
        screen_grant=None,
    )
    monkeypatch.setattr(
        device_screen_policy.tcp_connection_manager,
        "get_connection",
        lambda _: connection,
    )

    async def request(method, params, timeout):
        if method == "screen/status":
            return grant().model_dump()
        connection.connection_instance_id = "replacement"
        return {
            "protocol_version": 1,
            "revision": 1,
            "digest": desired.digest,
            "grant_id": desired.grant_id,
        }

    connection.send_request = request
    assert not await apply_screen_policy(connection, desired)
    assert connection.applied_screen_policy_digest is None


@pytest.mark.asyncio
async def test_negotiated_screen_tools_are_not_published_business_access(monkeypatch):
    connection = SimpleNamespace(
        desired_read_policy_digest=None,
        applied_screen_policy_digest="a" * 64,
        list_tools=AsyncMock(
            return_value=[{"name": "fs_read"}, {"name": "screen_screenshot"}]
        ),
        call_tool=AsyncMock(),
    )
    monkeypatch.setattr(
        mcp_server.tcp_connection_manager, "get_connection", lambda _: connection
    )
    result = await mcp_server.mcp_proxy.handle_list_tools("owned-device")
    assert result == {"tools": [{"name": "fs_read"}]}
    result = await mcp_server.mcp_proxy.handle_call_tool(
        "owned-device",
        {
            "name": "screen_screenshot",
            "arguments": {},
        },
    )
    assert result["isError"] is True
    connection.call_tool.assert_not_awaited()
