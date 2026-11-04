"""Explicit employee bindings must resolve the selected immutable release."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.exceptions import ValidationError
from app.services import workflow_service
from app.services.workflow_service import WorkflowData, WorkflowServiceClient


@pytest.mark.asyncio
async def test_selected_history_version_is_verified_instead_of_current(
    monkeypatch,
) -> None:
    client = WorkflowServiceClient()
    client.get_workflows_batch = AsyncMock(return_value=[WorkflowData(
        id="workflow-1", name="订单查询", status="active", version=8,
        published_version=4, updated_at="2026-10-14T00:00:00Z",
    )])
    requested_urls: list[str] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            requested_urls.append(url)
            return httpx.Response(
                200, request=httpx.Request("GET", url),
                json={"workflow_id": "workflow-1", "release_version": 2,
                      "release_id": "release-2", "definition_digest": "a" * 64,
                      "input_parameters": []},
            )

    monkeypatch.setattr(workflow_service.httpx, "AsyncClient", lambda **_kw: FakeClient())
    resolved = await client.resolve_published_bindings(
        ["workflow-1"], "project-1", {"workflow-1": 2}
    )
    assert resolved["workflow-1"].release_version == 2
    assert requested_urls == [
        f"{client.base_url}/v1/workflows/workflow-1/releases/2/integration"
    ]

    with pytest.raises(ValidationError, match="必须已关联"):
        await client.resolve_published_bindings(
            ["workflow-1"], "project-1", {"another-workflow": 2}
        )
