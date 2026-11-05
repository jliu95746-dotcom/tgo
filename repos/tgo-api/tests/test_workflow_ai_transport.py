"""Staff ownership is preserved in both streaming and JSON service calls."""
from uuid import uuid4
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import settings
from app.services.workflow_client import WorkflowServiceClient


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_staff_headers_reach_workflow_service(monkeypatch, stream):
    monkeypatch.setattr(settings, "SECRET_KEY", "synthetic-workflow-transport")
    monkeypatch.setattr(settings, "WORKFLOW_SERVICE_API_KEY", None)
    original = httpx.AsyncClient
    requests = []

    def reply(request):
        requests.append(request)
        if stream:
            return httpx.Response(
                200, text='data: {"event":"workflow_started"}\n\n'
            )
        response = httpx.Response(200, json={"success": True})
        response.elapsed = timedelta(milliseconds=1)
        return response

    def client(**options):
        return original(**options, transport=httpx.MockTransport(reply))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    service = WorkflowServiceClient()
    service.base_url = "http://workflow.internal"
    project, staff = uuid4(), uuid4()
    result = await service.execute_workflow(
        "owned",
        str(project),
        {"stream": stream, "inputs": {}},
        staff_id=staff,
        staff_token_version=7,
    )
    if stream:
        assert [frame async for frame in result] == [
            'data: {"event":"workflow_started"}\n\n'
        ]
    else:
        assert result == {"success": True}
    assert len(requests) == 1
    assert requests[0].headers["X-Workflow-Staff-ID"] == str(staff)
    assert requests[0].headers["X-Workflow-Staff-Version"] == "7"
    assert (
        requests[0].headers["X-Internal-API-Key"]
        == "synthetic-workflow-transport"
    )
    assert requests[0].url.params["project_id"] == str(project)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node_type,status,output,available",
    [
        ("answer", "completed", {"answer": "已保存的答复"}, True),
        ("answer", "cancelled", {"answer": "未完成"}, False),
        ("answer", "completed", {"answer": "  "}, False),
        ("agent", "completed", {"content": "内部生成结果"}, False),
    ],
)
async def test_usage_outcome_requires_saved_completed_answer(
    monkeypatch,
    node_type,
    status,
    output,
    available,
):
    service = WorkflowServiceClient()
    project, execution = uuid4(), uuid4()
    stored = {
        "project_id": str(project),
        "id": str(execution),
        "workflow_id": str(uuid4()),
        "status": "cancelled",
        "started_at": "2026-11-05T12:00:00Z",
        "node_executions": [
            {
                "id": str(uuid4()),
                "execution_id": str(execution),
                "node_id": "reply",
                "node_type": node_type,
                "status": status,
                "output": output,
                "started_at": "2026-11-05T12:00:00Z",
            }
        ],
    }
    read = AsyncMock(return_value=stored)
    monkeypatch.setattr(service, "get_execution", read)
    outcome = await service.workflow_usage_outcome(project, execution)
    assert outcome.answer_available is available
    assert outcome.project_id == project
    assert outcome.execution_id == execution
    read.assert_awaited_once_with(str(execution), str(project))
