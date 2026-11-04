"""Late acknowledgement cannot enable a replacement connection."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.schemas.device_script_policy import ScriptPolicyAck
from app.services import device_script_connection as module
from app.services.mcp_server import MCPProxy
from app.services.device_read_policy import set_desired_policy
from app.services.device_script_publication import stored_script_policy
from test_device_policy_switch import read_policy
from test_script_publication import connection, row
from test_script_policy_contract import descriptor


@pytest.mark.asyncio
async def test_replacement_during_ack_cannot_get_current_confirmation(
    monkeypatch,
):
    project, device = uuid4(), uuid4()
    old, replacement = connection(project, device), connection(project, device)
    current = [old]
    policy = stored_script_policy(row(project, device))
    monkeypatch.setattr(
        module.tcp_connection_manager, "get_connection", lambda _: current[0]
    )

    async def reply(method, params, timeout):
        if method == "scripts/status":
            return dict(
                protocol_version=1,
                state="awaiting_policy",
                session_id="d" * 32,
                reason="policy_required",
            )
        if method == "scripts/list":
            return dict(
                protocol_version=1, scripts=[descriptor().model_dump()]
            )
        current[0] = replacement
        return ScriptPolicyAck(
            protocol_version=1,
            session_id="d" * 32,
            revision=policy.revision,
            digest=policy.digest,
        ).model_dump()

    old.send_request.side_effect = reply
    assert not await module.apply_script_policy(old, policy)
    assert old.applied_script_policy_ack is None
    assert replacement.applied_script_policy_ack is None


@pytest.mark.asyncio
async def test_script_configuration_never_falls_through_to_generic_mcp(
    monkeypatch,
):
    project, device = uuid4(), uuid4()
    conn = connection(project, device)
    conn.script_policy_required = True
    conn.list_tools = AsyncMock(return_value=[{"name": "shell_exec"}])
    monkeypatch.setattr(
        module.tcp_connection_manager, "get_connection", lambda _: conn
    )
    result = await MCPProxy().handle_list_tools(str(device))
    assert "error" in result
    conn.list_tools.assert_not_awaited()


def test_file_mode_clears_script_confirmation():
    project, device = uuid4(), uuid4()
    conn = connection(project, device)
    conn.script_policy_required = True
    conn.desired_script_policy = object()
    set_desired_policy(conn, read_policy(project, device))
    assert conn.script_policy_required is False
    assert conn.desired_script_policy is None
    assert conn.applied_script_policy_ack is None
