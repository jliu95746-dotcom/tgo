"""The advertised node contract must agree with executable, guarded entry points."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import executions
from app.engine.nodes.registry import get_executor_class
from app.schemas.execution import WorkflowExecuteRequest
from app.services.node_readiness import workflow_capabilities, node_configuration_errors


def test_supported_nodes_have_executors_and_required_configuration():
    capabilities = workflow_capabilities()
    nodes = {node.type: node for node in capabilities.nodes}
    assert nodes["tool"].available is True
    assert nodes["tool"].required_fields == ["tool_id"]
    assert nodes["parallel"].available is True
    assert nodes["timer"].available is False
    assert nodes["webhook"].available is False
    assert nodes["webhook"].preparation_available is True
    assert all(get_executor_class(node.type) is not None for node in nodes.values() if node.available)
    assert capabilities.execution_modes == ["draft_test", "production"]


@pytest.mark.asyncio
@pytest.mark.parametrize("request_options", [{}, {"stream": True}, {"async": True}])
async def test_production_entry_rejects_a_draft_before_any_execution(monkeypatch, request_options):
    monkeypatch.setattr(executions.WorkflowService, "get_by_id", AsyncMock(return_value=SimpleNamespace(
        status="draft", definition={}, version=1,
    )))
    create = AsyncMock()
    monkeypatch.setattr(executions, "_create_execution_record", create)
    with pytest.raises(HTTPException) as error:
        await executions.execute_workflow(
            "owned-workflow", WorkflowExecuteRequest(execution_mode="production", **request_options),
            "owned-project", AsyncMock(),
        )
    assert error.value.status_code == 409
    create.assert_not_awaited()


@pytest.mark.parametrize("invalid", [{"private": "token"}, 3, True, "   "])
def test_resource_identifiers_must_be_nonempty_strings(invalid):
    assert node_configuration_errors([{"id": "query", "type": "tool", "data": {"tool_id": invalid}}])


@pytest.mark.parametrize("method, body_type", [
    ("POST", "json"), ("PUT", "none"), ("PATCH", "none"),
    ("DELETE", "none"), ("GET", "json"),
])
def test_api_node_requires_read_only_request(method, body_type):
    errors = node_configuration_errors([{"id": "query", "type": "api", "data": {
        "label": "查询", "url": "https://example.test/orders",
        "method": method, "body_type": body_type,
    }}])
    assert any("GET" in error for error in errors)
