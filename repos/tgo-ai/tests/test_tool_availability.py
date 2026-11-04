"""The workflow engine can confirm only a current, project-owned tool."""

from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.models.tool import Tool, ToolSourceType, ToolType
from app.services.tool_executor import ToolExecutor


@pytest.mark.asyncio
async def test_private_tool_availability_rejects_foreign_and_removed_tools(
    db_session, test_project, client, monkeypatch,
):
    monkeypatch.setattr(settings, "secret_key", "synthetic-tool-key")
    tool = Tool(project_id=test_project.id, name="订单查询", tool_type=ToolType.FUNCTION)
    db_session.add(tool)
    await db_session.commit()
    tool_id = tool.id
    headers = {
        "X-Internal-API-Key": "synthetic-tool-key",
        "X-Project-Id": str(test_project.id),
    }
    path = f"/api/v1/tools/{tool_id}/availability"

    available = client.get(path, headers=headers)
    assert available.status_code == 200
    assert available.json() == {
        "tool_id": str(tool_id), "project_id": str(test_project.id),
        "available": True, "workflow_read_only": False,
    }
    unsafe = client.post(
        f"/api/v1/tools/{tool_id}/execute",
        headers={**headers, "X-Workflow-Tool-Read-Only": "1"},
        json={"input_data": {"order_no": "ORDER-1"}},
    )
    assert unsafe.status_code == 403
    assert client.get(path, headers={**headers, "X-Project-Id": str(uuid4())}).status_code == 404
    assert client.get(path).status_code in (401, 403)

    tool.soft_delete()
    await db_session.commit()
    assert client.get(path, headers=headers).status_code == 404


@pytest.mark.asyncio
async def test_workflow_tool_policy_only_allows_builtin_logistics_queries(
    db_session, test_project, client, monkeypatch,
):
    monkeypatch.setattr(settings, "secret_key", "synthetic-tool-key")
    headers = {
        "X-Internal-API-Key": "synthetic-tool-key",
        "X-Project-Id": str(test_project.id),
    }
    tools = []
    for kind in ("custom", "kuaidi100", "kdniao"):
        tool = Tool(
            project_id=test_project.id,
            name=f"logistics_{kind}",
            tool_type=ToolType.FUNCTION,
            tool_source_type=ToolSourceType.LOCAL,
            transport_type="http_webhook",
            endpoint="https://example.test/query",
            config={"logistics_provider": {"provider_name": kind, "provider_kind": kind}},
        )
        db_session.add(tool)
        tools.append(tool)
    await db_session.commit()

    monkeypatch.setattr(ToolExecutor, "register_tools", AsyncMock())
    execute = AsyncMock(return_value='{"tracking_no":"TRACK123"}')
    monkeypatch.setattr(ToolExecutor, "execute", execute)
    for tool in tools:
        allowed = tool.name != "logistics_custom"
        path = f"/api/v1/tools/{tool.id}"
        receipt = client.get(f"{path}/availability", headers=headers)
        assert receipt.status_code == 200
        assert receipt.json()["workflow_read_only"] is allowed
        response = client.post(
            f"{path}/execute",
            headers={**headers, "X-Workflow-Tool-Read-Only": "1"},
            json={"input_data": {"tracking_no": "TRACK123"}},
        )
        assert response.status_code == (200 if allowed else 403)
    assert execute.await_count == 2
