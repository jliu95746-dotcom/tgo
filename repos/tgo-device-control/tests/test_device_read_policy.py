"""Read policies remain pending until the device confirms the exact scope."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.device_scenario import DeviceScenarioDraft
from app.schemas.device_access import DeviceServicePrincipal
from app.models.device_read_policy import DeviceReadPolicy
from app.schemas.device_read_policy import DeviceReadPolicyPublish, DeviceReadPolicyTest
from app.services.device_read_policy import (
    DeviceReadPolicyService,
    allowed_read_arguments,
    apply_read_policy,
    read_policy_digest,
    set_desired_policy,
    valid_policy_directory,
)
from app.services import device_read_policy, mcp_server
from app.api.v1 import mcp as mcp_api


def test_scope_requires_absolute_directory_and_limited_read_arguments():
    assert read_policy_digest(2, "/srv/orders", 100, 1000) == (
        "1695ab62a403ca4c1ff56c1b291b0ad6cccb5a3ae97b9f5c0e768748e32f52bf"
    )
    assert valid_policy_directory(r"C:\Business\Orders")
    assert valid_policy_directory("/srv/business/orders")
    for directory in ("orders", "C:\\", "/", r"C:\Business\..\Secrets", "C:\\x\x00"):
        assert not valid_policy_directory(directory)
    allowed = {"path": r"C:\Business\Orders\today.txt", "limit": 30}
    assert allowed_read_arguments(r"C:\Business\Orders", 100, 1000, allowed)
    for arguments in (
        {"path": r"C:\Business\Orders2\today.txt", "limit": 30},
        {"path": r"C:\Business\Orders\..\Secrets\key.txt", "limit": 30},
        {"path": "today.txt", "limit": 30},
        {"path": allowed["path"]},
        {"path": allowed["path"], "limit": 101},
        {"path": allowed["path"], "limit": 10, "offset": -1},
        {"path": allowed["path"], "limit": True},
        {"path": allowed["path"], "limit": 10, "command": "echo"},
    ):
        assert not allowed_read_arguments(r"C:\Business\Orders", 100, 1000, arguments)


def test_read_test_requires_a_distinct_nonblank_marker():
    with pytest.raises(ValidationError):
        DeviceReadPolicyTest(path=r"C:\Business\Orders\proof.txt", expected_text="    ")
    with pytest.raises(ValidationError):
        DeviceReadPolicyTest(path=r"C:\Business\Orders\proof.txt", expected_text="abc")


@pytest.mark.asyncio
async def test_acknowledgement_must_match_revision_and_digest():
    policy = SimpleNamespace(
        revision=2,
        allowed_directory=r"C:\Business\Orders",
        max_lines=100,
        max_offset=1000,
        digest=read_policy_digest(2, r"C:\Business\Orders", 100, 1000),
    )
    connection = SimpleNamespace(
        desired_read_policy_digest=None,
        applied_read_policy_digest=None,
        send_request=AsyncMock(return_value={"revision": 1, "digest": policy.digest}),
    )
    set_desired_policy(connection, policy)
    assert not await apply_read_policy(connection, policy)
    assert connection.applied_read_policy_digest is None
    connection.send_request.return_value = {"revision": 2, "digest": policy.digest}
    assert await apply_read_policy(connection, policy)
    assert connection.applied_read_policy_digest == policy.digest


@pytest.mark.asyncio
async def test_pending_and_out_of_scope_calls_never_reach_device(monkeypatch):
    directory = r"C:\Business\Orders"
    digest = read_policy_digest(1, directory, 100, 1000)
    connection = SimpleNamespace(
        desired_read_policy_digest=digest,
        applied_read_policy_digest=None,
        read_policy_verified_digest=None,
        read_policy_directory=directory,
        read_policy_max_lines=100,
        read_policy_max_offset=1000,
        call_tool=AsyncMock(return_value={"content": [], "isError": False}),
    )
    monkeypatch.setattr(
        mcp_server.tcp_connection_manager, "get_connection", lambda _: connection
    )
    params = {
        "name": "fs_read",
        "arguments": {"path": directory + r"\today.txt", "limit": 20},
    }
    assert (await mcp_server.mcp_proxy.handle_call_tool("device-1", params))["isError"]
    connection.call_tool.assert_not_awaited()
    connection.applied_read_policy_digest = digest
    assert (
        await mcp_server.mcp_proxy.handle_call_tool("device-1", params)
    )["isError"]
    connection.call_tool.assert_not_awaited()
    connection.read_policy_verified_digest = digest
    assert (
        await mcp_server.mcp_proxy.handle_call_tool(
            "device-1", {"name": "shell_exec", "arguments": {"command": "whoami"}}
        )
    )["isError"]
    assert (
        await mcp_server.mcp_proxy.handle_call_tool(
            "device-1",
            {
                "name": "fs_read",
                "arguments": {"path": r"C:\Business\Secrets\x", "limit": 20},
            },
        )
    )["isError"]
    connection.call_tool.assert_not_awaited()
    assert not (await mcp_server.mcp_proxy.handle_call_tool("device-1", params))[
        "isError"
    ]
    connection.call_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_discovery_exposes_only_active_read_action(monkeypatch):
    digest = "a" * 64
    connection = SimpleNamespace(
        desired_read_policy_digest=digest,
        applied_read_policy_digest=None,
        read_policy_verified_digest=None,
        list_tools=AsyncMock(return_value=[
            {"name": "fs_read", "description": "read"},
            {"name": "shell_exec", "description": "command"},
        ]),
    )
    monkeypatch.setattr(
        mcp_server.tcp_connection_manager,
        "get_connection",
        lambda _: connection,
    )
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    pending = await mcp_server.mcp_proxy.handle_jsonrpc("device-1", request)
    assert pending is not None and "error" in pending
    connection.list_tools.assert_not_awaited()

    connection.applied_read_policy_digest = digest
    untested = await mcp_server.mcp_proxy.handle_jsonrpc("device-1", request)
    assert untested is not None and "error" in untested
    connection.list_tools.assert_not_awaited()
    connection.read_policy_verified_digest = digest
    active = await mcp_server.mcp_proxy.handle_jsonrpc("device-1", request)
    assert active is not None
    assert [tool["name"] for tool in active["result"]["tools"]] == ["fs_read"]

    connection.list_tools.return_value = [{"name": "shell_exec"}]
    missing = await mcp_server.mcp_proxy.handle_jsonrpc("device-1", request)
    assert missing is not None and "error" in missing

    connection.desired_read_policy_digest = None
    legacy = await mcp_server.mcp_proxy.handle_jsonrpc("device-1", request)
    assert legacy == {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "shell_exec"}]}}


@pytest.mark.asyncio
async def test_mcp_rejects_a_stale_signed_employee_policy(monkeypatch):
    project_id, device_id = uuid4(), uuid4()
    digest = "a" * 64
    connection = SimpleNamespace(
        desired_read_policy_digest=digest,
        applied_read_policy_digest=digest,
        read_policy_verified_digest=digest,
        read_policy_directory=r"C:\Business\Orders",
        read_policy_max_lines=100,
        read_policy_max_offset=1000,
        list_tools=AsyncMock(return_value=[{"name": "fs_read"}]),
        call_tool=AsyncMock(return_value={"content": [], "isError": False}),
    )
    monkeypatch.setattr(
        mcp_server.tcp_connection_manager,
        "get_connection",
        lambda _: connection,
    )
    principal = DeviceServicePrincipal(
        sub="tgo-ai", project_id=project_id, device_id=device_id,
        read_policy_digest="b" * 64,
    )
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    stale_list = await mcp_server.mcp_proxy.handle_jsonrpc(
        str(device_id), request, principal
    )
    assert stale_list is not None and "error" in stale_list
    connection.list_tools.assert_not_awaited()
    call = await mcp_server.mcp_proxy.handle_call_tool(
        str(device_id), {
            "name": "fs_read",
            "arguments": {"path": r"C:\Business\Orders\proof.txt", "limit": 10},
        }, principal,
    )
    assert call["isError"]
    connection.call_tool.assert_not_awaited()

    current = principal.model_copy(update={"read_policy_digest": digest})
    allowed = await mcp_server.mcp_proxy.handle_jsonrpc(
        str(device_id), request, current
    )
    assert allowed is not None and allowed["result"]["tools"][0]["name"] == "fs_read"


@pytest.mark.asyncio
async def test_ai_cannot_discover_or_call_tools_without_a_published_policy(monkeypatch):
    project_id, device_id = uuid4(), uuid4()
    connection = SimpleNamespace(
        desired_read_policy_digest=None,
        list_tools=AsyncMock(return_value=[{"name": "shell_exec"}]),
        call_tool=AsyncMock(return_value={"content": [], "isError": False}),
    )
    monkeypatch.setattr(
        mcp_server.tcp_connection_manager, "get_connection", lambda _: connection
    )
    principal = DeviceServicePrincipal(
        sub="tgo-ai", project_id=project_id, device_id=device_id,
        session_id=uuid4(),
    )
    discovery = await mcp_server.mcp_proxy.handle_jsonrpc(
        str(device_id), {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        principal,
    )
    assert discovery is not None
    assert discovery["error"]["message"] == "Device policy is not published"
    connection.list_tools.assert_not_awaited()

    async def record(_principal, _device_id, _tool_name, invoke):
        return await invoke()

    monkeypatch.setattr(mcp_server, "record_device_tool", record)
    result = await mcp_server.mcp_proxy.handle_call_tool(
        str(device_id),
        {"name": "shell_exec", "arguments": {"command": "whoami"}},
        principal,
    )
    assert result == {
        "content": [{"type": "text", "text": "Error: Device policy is not published"}],
        "isError": True,
    }
    connection.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_tool_call_requires_an_execution_session(monkeypatch):
    project_id, device_id = uuid4(), uuid4()
    digest = "a" * 64
    connection = SimpleNamespace(
        desired_read_policy_digest=digest,
        applied_read_policy_digest=digest,
        read_policy_verified_digest=digest,
        read_policy_directory=r"C:\Business\Orders",
        read_policy_max_lines=100,
        read_policy_max_offset=1000,
        call_tool=AsyncMock(return_value={"content": [], "isError": False}),
    )
    monkeypatch.setattr(
        mcp_server.tcp_connection_manager, "get_connection", lambda _: connection
    )
    principal = DeviceServicePrincipal(
        sub="tgo-ai", project_id=project_id, device_id=device_id,
        read_policy_digest=digest,
    )
    result = await mcp_server.mcp_proxy.handle_call_tool(
        str(device_id), {
            "name": "fs_read",
            "arguments": {"path": r"C:\Business\Orders\today.txt", "limit": 10},
        }, principal,
    )
    assert result == {
        "content": [{"type": "text", "text": "Error: Device execution session required"}],
        "isError": True,
    }
    connection.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_management_principal_cannot_execute_device_tools_without_policy(monkeypatch):
    """The project management token must not become an unguarded write channel."""
    project_id, device_id = uuid4(), uuid4()
    connection = SimpleNamespace(
        desired_read_policy_digest=None,
        call_tool=AsyncMock(return_value={"content": [], "isError": False}),
    )
    monkeypatch.setattr(
        mcp_server.tcp_connection_manager, "get_connection", lambda _: connection
    )
    principal = DeviceServicePrincipal(sub="tgo-api", project_id=project_id)
    with pytest.raises(HTTPException) as denied:
        await mcp_server.mcp_proxy.handle_call_tool(
            str(device_id),
            {"name": "fs_write", "arguments": {"path": "example", "content": "x"}},
            principal,
        )
    assert denied.value.status_code == 403
    connection.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_audited_tool_call_keeps_the_signed_policy(monkeypatch):
    project_id, device_id = uuid4(), uuid4()
    digest = "a" * 64
    connection = SimpleNamespace(
        desired_read_policy_digest=digest,
        applied_read_policy_digest=digest,
        read_policy_verified_digest=digest,
        read_policy_directory=r"C:\Business\Orders",
        read_policy_max_lines=100,
        read_policy_max_offset=1000,
        call_tool=AsyncMock(return_value={"content": [], "isError": False}),
    )
    monkeypatch.setattr(
        mcp_server.tcp_connection_manager, "get_connection", lambda _: connection
    )

    recorded = []

    async def record(_principal, _device_id, _tool_name, invoke):
        recorded.append((_principal.session_id, _tool_name))
        return await invoke()

    monkeypatch.setattr(mcp_server, "record_device_tool", record)
    principal = DeviceServicePrincipal(
        sub="tgo-ai", project_id=project_id, device_id=device_id,
        session_id=uuid4(), read_policy_digest="b" * 64,
    )
    result = await mcp_server.mcp_proxy.handle_call_tool(
        str(device_id), {
            "name": "fs_read",
            "arguments": {"path": r"C:\Business\Orders\proof.txt", "limit": 10},
        }, principal,
    )
    assert result["isError"]
    connection.call_tool.assert_not_awaited()
    current = principal.model_copy(update={"read_policy_digest": digest})
    allowed = await mcp_server.mcp_proxy.handle_call_tool(
        str(device_id), {
            "name": "fs_read",
            "arguments": {"path": r"C:\Business\Orders\proof.txt", "limit": 10},
        }, current,
    )
    assert allowed["isError"] is False
    assert recorded == [
        (principal.session_id, "fs_read"),
        (principal.session_id, "fs_read"),
    ]
    connection.call_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_rest_tool_discovery_returns_conflict_for_unverified_policy(monkeypatch):
    project_id, device_id = uuid4(), uuid4()
    monkeypatch.setattr(
        mcp_api.tcp_connection_manager, "get_connection", lambda _: object()
    )
    monkeypatch.setattr(
        mcp_api.mcp_proxy, "handle_list_tools",
        AsyncMock(return_value={"error": {
            "code": -32001,
            "message": "Device read action requires a current test",
        }}),
    )
    principal = DeviceServicePrincipal(
        sub="tgo-ai", project_id=project_id, device_id=device_id,
        read_policy_digest="a" * 64,
    )
    with pytest.raises(HTTPException) as error:
        await mcp_api.list_device_tools(device_id, principal)
    assert error.value.status_code == 409
    assert error.value.detail == "Device read action requires a current test"


@pytest.mark.asyncio
async def test_publish_uses_saved_draft_revision_and_stays_pending_without_device(
    monkeypatch,
):
    project_id, device_id = uuid4(), uuid4()
    draft = DeviceScenarioDraft(
        device_id=device_id,
        project_id=project_id,
        scenario="files",
        purpose="查看订单",
        target_directory=r"C:\Business\Orders",
        revision=4,
    )
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=[device_id, draft, None, None, None])
    db.commit = AsyncMock()

    async def refresh(policy):
        policy.updated_at = datetime.now(timezone.utc)

    db.refresh = AsyncMock(side_effect=refresh)
    monkeypatch.setattr(
        device_read_policy.tcp_connection_manager, "get_connection", lambda _: None
    )
    service = DeviceReadPolicyService(db)
    policy = await service.publish(
        project_id,
        device_id,
        DeviceReadPolicyPublish(expected_draft_revision=4, expected_policy_revision=0),
    )
    assert policy.revision == 1 and policy.status == "pending"
    assert policy.allowed_directory == draft.target_directory
    assert policy.digest == read_policy_digest(1, draft.target_directory, 100, 10000)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_draft_cannot_publish_policy():
    project_id, device_id = uuid4(), uuid4()
    draft = DeviceScenarioDraft(
        device_id=device_id,
        project_id=project_id,
        scenario="files",
        purpose="查看订单",
        target_directory=r"C:\Business\Orders",
        revision=4,
    )
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=[device_id, draft])
    db.commit = AsyncMock()
    with pytest.raises(HTTPException) as error:
        await DeviceReadPolicyService(db).publish(
            project_id,
            device_id,
            DeviceReadPolicyPublish(
                expected_draft_revision=3, expected_policy_revision=0
            ),
        )
    assert error.value.status_code == 409
    db.commit.assert_not_awaited()


def read_test_context(monkeypatch, *, active=True, tool_result=None):
    project_id, device_id = uuid4(), uuid4()
    directory = r"C:\Business\Orders"
    digest = read_policy_digest(1, directory, 100, 10000)
    policy = DeviceReadPolicy(
        project_id=project_id,
        device_id=device_id,
        revision=1,
        allowed_directory=directory,
        max_lines=100,
        max_offset=10000,
        digest=digest,
        updated_at=datetime.now(timezone.utc),
    )
    connection = SimpleNamespace(
        project_id=str(project_id),
        desired_read_policy_digest=digest,
        applied_read_policy_digest=digest if active else None,
        read_policy_verified_digest=None,
        connection_instance_id=uuid4().hex,
        call_tool=AsyncMock(return_value=tool_result),
    )
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=[device_id, policy])
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    monkeypatch.setattr(
        device_read_policy.tcp_connection_manager,
        "get_connection",
        lambda _: connection,
    )
    return project_id, device_id, policy, connection, db


@pytest.mark.asyncio
async def test_read_test_requires_active_policy_and_scoped_file(monkeypatch):
    project_id, device_id, _, connection, db = read_test_context(
        monkeypatch, active=False
    )
    request = DeviceReadPolicyTest(
        path=r"C:\Business\Orders\proof.txt", expected_text="test-marker"
    )
    with pytest.raises(HTTPException) as error:
        await DeviceReadPolicyService(db).test_read(project_id, device_id, request)
    assert error.value.status_code == 409
    connection.call_tool.assert_not_awaited()
    db.commit.assert_not_awaited()

    project_id, device_id, _, connection, db = read_test_context(monkeypatch)
    request.path = r"C:\Business\Secrets\proof.txt"
    with pytest.raises(HTTPException) as error:
        await DeviceReadPolicyService(db).test_read(project_id, device_id, request)
    assert error.value.status_code == 422
    connection.call_tool.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_test_receipt_is_bound_to_current_connection(monkeypatch):
    project_id, device_id, policy, connection, db = read_test_context(
        monkeypatch,
        tool_result={
            "isError": False,
            "content": [{"type": "text", "text": "1|test-marker\n"}],
        },
    )
    request = DeviceReadPolicyTest(
        path=r"C:\Business\Orders\proof.txt", expected_text="test-marker"
    )
    result = await DeviceReadPolicyService(db).test_read(project_id, device_id, request)
    assert result.verified and result.last_test_status == "passed"
    connection.call_tool.assert_awaited_once_with(
        "fs_read", {"path": request.path, "limit": 20}
    )
    assert policy.last_test_connection_id == connection.connection_instance_id
    assert connection.read_policy_verified_digest == policy.digest
    assert "test-marker" not in result.model_dump_json()
    assert DeviceReadPolicyService._response(policy).verified
    connection.connection_instance_id = uuid4().hex
    assert not DeviceReadPolicyService._response(policy).verified


@pytest.mark.asyncio
async def test_read_test_marker_mismatch_records_failure_without_content(monkeypatch):
    project_id, device_id, policy, connection, db = read_test_context(
        monkeypatch,
        tool_result={
            "isError": False,
            "content": [{"type": "text", "text": "1|private file contents\n"}],
        },
    )
    result = await DeviceReadPolicyService(db).test_read(
        project_id,
        device_id,
        DeviceReadPolicyTest(
            path=r"C:\Business\Orders\proof.txt", expected_text="test-marker"
        ),
    )
    assert not result.verified and result.last_test_status == "failed"
    assert policy.last_test_status == "failed"
    assert connection.read_policy_verified_digest is None
    assert "private file contents" not in result.model_dump_json()
    db.commit.assert_awaited_once()
    connection.call_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_republishing_policy_clears_previous_read_test(monkeypatch):
    project_id, device_id, policy, _, db = read_test_context(monkeypatch)
    draft = DeviceScenarioDraft(
        device_id=device_id,
        project_id=project_id,
        scenario="files",
        purpose="查看订单",
        target_directory=policy.allowed_directory,
        revision=2,
    )
    policy.last_tested_at = datetime.now(timezone.utc)
    policy.last_test_status = "passed"
    policy.last_test_digest = policy.digest
    policy.last_test_connection_id = "old-connection"
    db.scalar = AsyncMock(side_effect=[device_id, draft, policy, None, None])
    monkeypatch.setattr(
        device_read_policy.tcp_connection_manager,
        "get_connection",
        lambda _: None,
    )
    result = await DeviceReadPolicyService(db).publish(
        project_id,
        device_id,
        DeviceReadPolicyPublish(expected_draft_revision=2, expected_policy_revision=1),
    )
    assert result.revision == 2 and not result.verified
    assert result.last_test_status is None
    assert policy.last_tested_at is None
    assert policy.last_test_digest is None
    assert policy.last_test_connection_id is None
