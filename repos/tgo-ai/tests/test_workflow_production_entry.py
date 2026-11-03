"""An employee invokes the guarded production entry rather than an editor test."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4
import json

import pytest

from app.runtime.tools import utils


class Response:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    async def json(self):
        return self.payload


@pytest.mark.asyncio
async def test_approval_tool_submits_async_once_per_invocation(monkeypatch):
    session = Response(None)
    session.get = Mock(side_effect=[
        Response([{"id": "approval-workflow", "name": "售后核对", "status": "active",
                   "published_version": 2}]),
        Response({"workflow_id": "approval-workflow", "release_version": 2,
                  "release_id": "fixed-release", "definition_digest": "a" * 64,
                  "input_parameters": [], "node_types": ["input", "approval", "answer"]}),
    ])
    session.post = Mock(return_value=Response({"id": "run-1", "status": "pending",
                                               "input": {"private": "customer text"}}))
    monkeypatch.setattr(utils.aiohttp, "ClientSession", Mock(return_value=session))
    monkeypatch.setattr(utils, "verify_current_binding", AsyncMock())
    tools = await utils.create_workflow_tools(
        "https://workflow.example.test", ["approval-workflow"], "owned-project",
        agent_id="agent-1", request_id="ai-run-1",
        fixed_versions={"approval-workflow": 2},
        release_ids={"approval-workflow": "fixed-release"},
        release_digests={"approval-workflow": "a" * 64},
    )
    first = json.loads(await tools[0].entrypoint({"question": "退货"}))
    first_payload = session.post.call_args.kwargs["json"]
    second = json.loads(await tools[0].entrypoint({"question": "退货"}))
    second_payload = session.post.call_args.kwargs["json"]
    assert first == second == {"success": True, "status": "pending", "execution_id": "run-1"}
    assert first_payload["async"] is True and first_payload["stream"] is False
    assert first_payload["invocation_key"] == second_payload["invocation_key"]
    assert first_payload["origin"] == {"ai_request_id": "ai-run-1"}


@pytest.mark.asyncio
async def test_employee_workflow_call_uses_production_mode_and_owned_scope(monkeypatch):
    session = Response(None)
    session.get = Mock(side_effect=[
        Response([{"id": "owned-workflow", "name": "查询", "status": "active", "published_version": 3}]),
        Response({"workflow_id": "owned-workflow", "release_version": 3,
                  "release_id": "fixed-release", "definition_digest": "a" * 64,
                  "input_parameters": []}),
    ])
    session.post = Mock(return_value=Response({"success": True, "output": "accepted"}))
    monkeypatch.setattr(utils.aiohttp, "ClientSession", Mock(return_value=session))
    verify_binding = AsyncMock()
    monkeypatch.setattr(utils, "verify_current_binding", verify_binding)
    tools = await utils.create_workflow_tools(
        "https://workflow.example.test", ["owned-workflow"], "owned-project",
        agent_id="agent-1",
        request_id="ai-run-only",
        fixed_versions={"owned-workflow": 3},
        release_ids={"owned-workflow": "fixed-release"},
        release_digests={"owned-workflow": "a" * 64},
    )
    result = await tools[0].entrypoint({"question": "owned-input"})
    assert '"success": true' in result
    call = session.post.call_args
    assert call.kwargs["json"]["execution_mode"] == "production"
    assert call.kwargs["json"]["release_version"] == 3
    assert call.kwargs["json"]["employee_agent_id"] == "agent-1"
    assert call.kwargs["json"]["origin"] == {"ai_request_id": "ai-run-only"}
    assert call.kwargs["headers"]["X-Internal-API-Key"]
    assert call.kwargs["params"]["project_id"] == "owned-project"
    verify_binding.assert_awaited_once_with(
        "agent-1", "owned-project", "owned-workflow", 3, "fixed-release", "a" * 64,
    )


@pytest.mark.asyncio
async def test_nested_employee_workflow_passes_parent_lineage(monkeypatch):
    parent_id = uuid4()
    session = Response(None)
    session.get = Mock(side_effect=[
        Response([{"id": "workflow-b", "name": "查询", "status": "active", "published_version": 3}]),
        Response({"workflow_id": "workflow-b", "release_version": 3,
                  "release_id": "fixed-release", "definition_digest": "a" * 64,
                  "input_parameters": []}),
    ])
    session.post = Mock(return_value=Response({"success": True}))
    monkeypatch.setattr(utils.aiohttp, "ClientSession", Mock(return_value=session))
    monkeypatch.setattr(utils, "verify_current_binding", AsyncMock())
    tools = await utils.create_workflow_tools(
        "https://workflow.example.test", ["workflow-b"], "owned-project",
        agent_id="agent-1", request_id="ai-run-nested",
        fixed_versions={"workflow-b": 3},
        release_ids={"workflow-b": "fixed-release"},
        release_digests={"workflow-b": "a" * 64},
        parent_workflow_execution_id=parent_id,
        workflow_lineage=("workflow-a",),
    )
    await tools[0].entrypoint({})
    assert session.post.call_args.kwargs["json"]["origin"] == {
        "ai_request_id": "ai-run-nested",
        "parent_execution_id": str(parent_id),
        "workflow_lineage": ["workflow-a"],
    }


@pytest.mark.asyncio
async def test_employee_workflow_carries_verified_customer_origin_only(monkeypatch):
    from tests.test_business_execution_context import fixture

    check, principal = fixture()
    session = Response(None)
    session.get = Mock(side_effect=[
        Response([{"id": "owned-workflow", "name": "查询", "status": "active", "published_version": 3}]),
        Response({"workflow_id": "owned-workflow", "release_version": 3,
                  "release_id": "fixed-release", "definition_digest": "a" * 64,
                  "input_parameters": []}),
    ])
    session.post = Mock(return_value=Response({"success": True, "output": "accepted"}))
    monkeypatch.setattr(utils.aiohttp, "ClientSession", Mock(return_value=session))
    monkeypatch.setattr(utils, "verify_current_binding", AsyncMock())
    tools = await utils.create_workflow_tools(
        "https://workflow.example.test", ["owned-workflow"], str(check.project_id),
        agent_id=str(check.visitor_id), request_id="ai-run-a",
        business_principal=principal,
        business_context_token=check.context_token,
        fixed_versions={"owned-workflow": 3},
        release_ids={"owned-workflow": "fixed-release"},
        release_digests={"owned-workflow": "a" * 64},
    )
    await tools[0].entrypoint({"question": "test"})
    origin = session.post.call_args.kwargs["json"]["origin"]
    assert origin == {
        "ai_request_id": "ai-run-a",
        "business_project_id": str(check.project_id),
        "business_request_id": str(check.request_id),
        "visitor_session_id": str(principal.visitor_session_id),
        "source_message_id": principal.source_message_id,
        "source_channel": principal.channel.value,
    }
    assert check.context_token.get_secret_value() not in str(session.post.call_args.kwargs["json"])
    with pytest.raises(ValueError, match="项目"):
        await utils.create_workflow_tools(
            "https://workflow.example.test", ["owned-workflow"], str(uuid4()),
            agent_id=str(check.visitor_id), request_id="ai-run-a",
            business_principal=principal,
            business_context_token=check.context_token,
            fixed_versions={"owned-workflow": 3},
            release_ids={"owned-workflow": "fixed-release"},
            release_digests={"owned-workflow": "a" * 64},
        )


@pytest.mark.asyncio
async def test_customer_context_is_forwarded_only_in_private_headers(monkeypatch):
    from tests.test_business_execution_context import fixture

    check, principal = fixture()
    session = Response(None)
    session.get = Mock(side_effect=[
        Response([{"id": "owned-workflow", "name": "查询", "status": "active", "published_version": 3}]),
        Response({"workflow_id": "owned-workflow", "release_version": 3,
                  "release_id": "fixed-release", "definition_digest": "a" * 64,
                  "input_parameters": []}),
    ])
    session.post = Mock(return_value=Response({"success": True}))
    monkeypatch.setattr(utils.aiohttp, "ClientSession", Mock(return_value=session))
    monkeypatch.setattr(utils, "verify_current_binding", AsyncMock())
    tools = await utils.create_workflow_tools(
        "https://workflow.example.test", ["owned-workflow"], str(check.project_id),
        agent_id=str(uuid4()), request_id="ai-run-a",
        business_principal=principal, business_context_token=check.context_token,
        fixed_versions={"owned-workflow": 3},
        release_ids={"owned-workflow": "fixed-release"},
        release_digests={"owned-workflow": "a" * 64},
    )
    await tools[0].entrypoint({"question": "test"})
    call = session.post.call_args.kwargs
    assert call["headers"]["X-Business-Request-Context"] == check.context_token.get_secret_value()
    assert call["headers"]["X-Business-Visitor-Id"] == str(check.visitor_id)
    assert call["headers"]["X-Business-Request-Fingerprint"] == check.request_fingerprint
    assert call["headers"]["X-Execution-Channel"] == check.channel.value
    assert check.context_token.get_secret_value() not in str(call["json"])


@pytest.mark.asyncio
async def test_customer_workflow_rejects_missing_context_token_before_discovery():
    from tests.test_business_execution_context import fixture

    check, principal = fixture()
    with pytest.raises(ValueError, match="上下文凭证"):
        await utils.create_workflow_tools(
            "https://workflow.example.test", ["owned-workflow"], str(check.project_id),
            agent_id=str(uuid4()), request_id="ai-run-a",
            business_principal=principal,
            fixed_versions={"owned-workflow": 3},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status,version", [
    ("draft", 1), ("active", None), ("active", 0),
    ("active", True), ("active", "3"), ("archived", 3),
])
async def test_unpublished_or_invalid_release_is_not_exposed_to_the_employee(monkeypatch, status, version):
    session = Response(None)
    session.get = Mock(return_value=Response([{
        "id": "owned-workflow", "name": "查询", "status": status,
        "published_version": version, "input_parameters": [],
    }]))
    session.post = Mock()
    monkeypatch.setattr(utils.aiohttp, "ClientSession", Mock(return_value=session))
    with pytest.raises(ValueError, match="请先发布"):
        await utils.create_workflow_tools(
            "https://workflow.example.test", ["owned-workflow"], "owned-project",
            agent_id="agent-1",
            fixed_versions={"owned-workflow": 3},
            release_ids={"owned-workflow": "fixed-release"},
            release_digests={"owned-workflow": "a" * 64},
        )
    session.post.assert_not_called()


@pytest.mark.asyncio
async def test_bound_employee_uses_original_release_and_input_contract(monkeypatch):
    session = Response(None)
    session.get = Mock(side_effect=[
        Response([{"id": "owned-workflow", "name": "查询", "status": "active", "published_version": 4, "input_parameters": [{"name": "new_field", "required": True}]}]),
        Response({"workflow_id": "owned-workflow", "release_version": 2,
                  "release_id": "fixed-release", "definition_digest": "a" * 64,
                  "input_parameters": [{"name": "order_id", "type": "string", "required": True}]}),
    ])
    session.post = Mock(return_value=Response({"success": True}))
    monkeypatch.setattr(utils.aiohttp, "ClientSession", Mock(return_value=session))
    monkeypatch.setattr(utils, "verify_current_binding", AsyncMock())
    tools = await utils.create_workflow_tools(
        "https://workflow.example.test", ["owned-workflow"], "owned-project",
        agent_id="agent-1",
        fixed_versions={"owned-workflow": 2},
        release_ids={"owned-workflow": "fixed-release"},
        release_digests={"owned-workflow": "a" * 64},
    )
    assert "order_id" in tools[0].parameters["properties"]["inputs"]["properties"]
    await tools[0].entrypoint({"order_id": "test-order"})
    assert session.post.call_args.kwargs["json"]["release_version"] == 2
    assert session.post.call_args.kwargs["json"]["expected_release_id"] == "fixed-release"
    assert session.post.call_args.kwargs["json"]["expected_release_digest"] == "a" * 64


@pytest.mark.asyncio
async def test_bound_employee_rejects_mismatched_release_digest(monkeypatch):
    session = Response(None)
    session.get = Mock(side_effect=[
        Response([{"id": "owned-workflow", "status": "active", "published_version": 4}]),
        Response({"workflow_id": "owned-workflow", "release_version": 2,
                  "release_id": "fixed-release", "definition_digest": "b" * 64,
                  "input_parameters": []}),
    ])
    monkeypatch.setattr(utils.aiohttp, "ClientSession", Mock(return_value=session))
    with pytest.raises(ValueError, match="发布版本校验"):
        await utils.create_workflow_tools(
            "https://workflow.example.test", ["owned-workflow"], "owned-project",
            agent_id="agent-1",
            fixed_versions={"owned-workflow": 2},
            release_ids={"owned-workflow": "fixed-release"},
            release_digests={"owned-workflow": "a" * 64},
        )


@pytest.mark.asyncio
async def test_employee_workflow_requires_a_persisted_release(monkeypatch):
    session = Response(None)
    session.get = Mock(return_value=Response([{
        "id": "owned-workflow", "status": "active", "published_version": 3,
    }]))
    monkeypatch.setattr(utils.aiohttp, "ClientSession", Mock(return_value=session))
    with pytest.raises(ValueError, match="尚未固定发布版本"):
        await utils.create_workflow_tools(
            "https://workflow.example.test", ["owned-workflow"], "owned-project",
            agent_id="agent-1",
        )
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_bound_employee_rejects_replaced_release_row(monkeypatch):
    session = Response(None)
    session.get = Mock(side_effect=[
        Response([{"id": "owned-workflow", "status": "active", "published_version": 4}]),
        Response({"workflow_id": "owned-workflow", "release_version": 2,
                  "release_id": "another-release", "definition_digest": "a" * 64,
                  "input_parameters": []}),
    ])
    session.post = Mock()
    monkeypatch.setattr(utils.aiohttp, "ClientSession", Mock(return_value=session))
    with pytest.raises(ValueError, match="发布版本校验"):
        await utils.create_workflow_tools(
            "https://workflow.example.test", ["owned-workflow"], "owned-project",
            agent_id="agent-1",
            fixed_versions={"owned-workflow": 2},
            release_ids={"owned-workflow": "fixed-release"},
            release_digests={"owned-workflow": "a" * 64},
        )
    session.post.assert_not_called()


@pytest.mark.asyncio
async def test_revoked_employee_binding_never_posts_workflow_execution(monkeypatch):
    session = Response(None)
    session.get = Mock(side_effect=[
        Response([{"id": "owned-workflow", "name": "查询", "status": "active", "published_version": 3}]),
        Response({"workflow_id": "owned-workflow", "release_version": 3,
                  "release_id": "fixed-release", "definition_digest": "a" * 64,
                  "input_parameters": []}),
    ])
    session.post = Mock()
    monkeypatch.setattr(utils.aiohttp, "ClientSession", Mock(return_value=session))
    monkeypatch.setattr(
        utils, "verify_current_binding",
        AsyncMock(side_effect=PermissionError("员工工作流授权已变更，停止执行。")),
    )
    tools = await utils.create_workflow_tools(
        "https://workflow.example.test", ["owned-workflow"], "owned-project",
        agent_id="agent-1",
        fixed_versions={"owned-workflow": 3},
        release_ids={"owned-workflow": "fixed-release"},
        release_digests={"owned-workflow": "a" * 64},
    )

    result = await tools[0].entrypoint({"question": "owned-input"})
    assert '"success": false' in result
    assert "员工工作流授权已变更" in result
    session.post.assert_not_called()
