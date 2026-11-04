"""Only a trusted internal workflow hop can supply parent coordinates."""

import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.schemas.knowledge import KnowledgeChannel
from app.services.workflow_parent_context import trusted_workflow_parent


def _request(headers: dict[str, str]) -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/api/v1/agents/run",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
    })


def test_workflow_parent_is_only_accepted_for_internal_channel():
    parent_id = uuid4()
    headers = {
        "X-Workflow-Lineage": json.dumps(["workflow-a"]),
        "X-Workflow-Parent-Execution-ID": str(parent_id),
    }
    with pytest.raises(HTTPException) as error:
        trusted_workflow_parent(_request(headers), KnowledgeChannel.WEB)
    assert error.value.status_code == 403
    context = trusted_workflow_parent(_request(headers), KnowledgeChannel.INTERNAL)
    assert context.parent_execution_id == parent_id
    assert context.workflow_lineage == ("workflow-a",)


@pytest.mark.parametrize("lineage", [
    [], ["workflow-a", "workflow-a"], ["workflow-a"] * 5, [123],
])
def test_workflow_parent_rejects_invalid_lineage(lineage):
    with pytest.raises(HTTPException) as error:
        trusted_workflow_parent(_request({
            "X-Workflow-Lineage": json.dumps(lineage),
            "X-Workflow-Parent-Execution-ID": str(uuid4()),
        }), KnowledgeChannel.INTERNAL)
    assert error.value.status_code == 403
