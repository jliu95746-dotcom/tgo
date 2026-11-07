"""Resource configuration rejects invalid inputs before side effects."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app.api.v1 import tools as tool_api
from app.config import Settings
from app.exceptions import ConflictError, ValidationError
from app.models.agent_business_connection import AgentBusinessConnection
from app.models.internal import AgentTool
from app.runtime.tools.custom import user_tag
from app.schemas.agent_business_reviews import BusinessOperationCapability
from app.services.agent_business_capabilities import (
    AgentBusinessCapabilityService,
)
from app.services.agent_humanization import validate_humanization_binding
from app.services.skill_file_service import SkillFileService
from tests.test_agent_business_reviews import review_fixture as fixture_source
from tests.test_agent_business_trials import trial as trial_source

review_fixture = fixture_source
trial = trial_source
PRIVATE = "SYNTHETIC-RESOURCE-PRIVATE"


@pytest.mark.parametrize("name", [None, PRIVATE])
@pytest.mark.asyncio
async def test_null_expression_switch_is_rejected_before_skill_lookup(
    name,
    monkeypatch,
):
    lookup = AsyncMock()
    lookup.return_value.skill_type = "humanization"
    monkeypatch.setattr(SkillFileService, "get_skill", lookup)
    with pytest.raises(ValidationError):
        await validate_humanization_binding(uuid4(), name, None)
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_expression_without_a_skill_remains_valid():
    await validate_humanization_binding(uuid4(), None, False)


def make_tool(base_config):
    return AgentTool(
        tool_id=uuid4(),
        tool_name="lookup",
        tool_type="MCP",
        base_config=base_config,
    )


@pytest.mark.parametrize(
    "value", [True, PRIVATE, 12, [PRIVATE], {"x": object()}]
)
def test_truthy_non_json_object_schema_is_rejected(value):
    with pytest.raises(ValueError) as captured:
        _ = make_tool({"inputSchema": value}).input_schema
    assert PRIVATE not in str(captured.value)


@pytest.mark.parametrize("value", [None, False, 0, "", [], {}])
def test_empty_schema_keeps_the_existing_fallback(value):
    assert make_tool({"inputSchema": value}).input_schema == {
        "type": "object",
        "properties": {},
    }


def test_schema_spelling_priority_and_nested_data_are_preserved():
    value = {"type": "object", "properties": {"order": {"type": "string"}}}
    assert make_tool({"inputSchema": value}).input_schema == value
    assert (
        make_tool({"inputSchema": {}, "input_schema": value}).input_schema
        == value
    )
    assert (
        make_tool(
            {"inputSchema": value, "input_schema": {"type": "array"}}
        ).input_schema
        == value
    )


async def binding_from_fixture(fixture):
    db, _, _, _, project, _, agent, *_ = fixture
    binding = (
        await db.scalars(
            select(AgentBusinessConnection).where(
                AgentBusinessConnection.project_id == project,
                AgentBusinessConnection.agent_id == agent,
            )
        )
    ).first()
    assert binding is not None
    return binding


@pytest.mark.parametrize(
    "operations",
    [
        [PRIVATE],
        ["order_query", PRIVATE],
        "order_query",
        None,
        {"key": PRIVATE},
        ["order_query", "order_query"],
    ],
)
@pytest.mark.asyncio
async def test_corrupt_persisted_operations_are_rejected_before_remote_checks(
    review_fixture,
    operations,
    monkeypatch,
):
    db, _, _, _, project, actor, agent, _, reviews, _ = review_fixture
    binding = await binding_from_fixture(review_fixture)
    binding.operations = operations
    await db.commit()
    service = AgentBusinessCapabilityService(db, reviews)
    check = AsyncMock()
    monkeypatch.setattr(service, "_operation", check)
    with pytest.raises(ConflictError) as captured:
        await service.read(project, actor, agent)
    assert PRIVATE not in str(captured.value)
    check.assert_not_awaited()


@pytest.mark.parametrize("timeout", [False, True])
@pytest.mark.asyncio
async def test_operation_order_and_timeout_result_are_preserved(
    review_fixture,
    monkeypatch,
    timeout,
):
    db, _, _, _, project, actor, agent, _, reviews, _ = review_fixture
    binding = await binding_from_fixture(review_fixture)
    operations = ["logistics_query", "order_query"]
    binding.operations = operations
    await db.commit()
    service = AgentBusinessCapabilityService(db, reviews)

    async def check(project, actor, agent, row, operation, contexts):
        if timeout:
            raise TimeoutError
        return BusinessOperationCapability(
            binding_id=row.id,
            binding_revision=row.revision,
            connection_id=row.connection_id,
            operation=operation,
            status="unavailable",
            report_id=None,
            review_id=None,
            valid_until=None,
            issues=["evidence_unavailable"],
        )

    monkeypatch.setattr(service, "_operation", check)
    result = await service.read(project, actor, agent)
    assert [value.operation for value in result.capabilities] == operations
    assert not result.can_execute


@pytest.mark.parametrize("tag", [None, PRIVATE, {"name": None}, {"name": 12}])
@pytest.mark.asyncio
async def test_invalid_tag_never_posts_an_event(tag, monkeypatch):
    post = AsyncMock()
    monkeypatch.setattr(user_tag.EventClient, "post_event", post)
    tool = user_tag.create_user_tag_tool(
        agent_id="agent",
        session_id=None,
        user_id="visitor",
    )
    result = await tool.entrypoint(tags=[tag])
    assert "标签" in result
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_tag_vocabulary_aliases_and_payload_are_preserved(monkeypatch):
    post = AsyncMock(return_value=SimpleNamespace(success=True))
    monkeypatch.setattr(user_tag.EventClient, "post_event", post)
    tool = user_tag.create_user_tag_tool(
        agent_id="agent",
        session_id=None,
        user_id="visitor",
    )
    await tool.entrypoint(
        tags=[
            {"name": "VIP", "name_zh": "ignored"},
            {"name": "vip_customer"},
            {"name": "after-sales"},
        ]
    )
    assert post.await_args.args == (
        "user_tag.add",
        {
            "tags": [
                {"name": "vip_customer", "name_zh": "重要客户"},
                {"name": "after_sales", "name_zh": "售后服务"},
            ],
            "metadata": {"source": "ai_analysis"},
        },
    )


@pytest.mark.parametrize("endpoint", [12, True, [PRIVATE], {"url": PRIVATE}])
def test_invalid_logistics_endpoint_is_rejected_before_preparation(
    endpoint,
    monkeypatch,
):
    prepare = Mock()
    monkeypatch.setattr(tool_api, "prepare_config", prepare)
    with pytest.raises(HTTPException) as captured:
        tool_api._prepare_logistics_tool(
            {
                "config": {"logistics_provider": {"name": "test"}},
                "endpoint": endpoint,
            }
        )
    assert captured.value.status_code == 422
    assert PRIVATE not in str(captured.value.detail)
    prepare.assert_not_called()


@pytest.mark.parametrize("environment", [False, True])
def test_runtime_dotenv_and_environment_precedence(
    tmp_path,
    monkeypatch,
    environment,
):
    for key in list(os.environ):
        if key.upper().startswith(("TOOLS_RUNTIME", "SUPERVISOR_RUNTIME")):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "SUPERVISOR_RUNTIME__ENABLE_STREAMING=false\n"
        "TOOLS_RUNTIME__MCP__DEFAULT_URL=https://file.example.test\n",
        encoding="utf-8",
    )
    if environment:
        monkeypatch.setenv("SUPERVISOR_RUNTIME__ENABLE_STREAMING", "true")
        monkeypatch.setenv(
            "TOOLS_RUNTIME__MCP__DEFAULT_URL",
            "https://environment.example.test",
        )
    candidate = Settings()
    assert candidate.supervisor_runtime.enable_streaming is environment
    assert candidate.tools_runtime.mcp.default_url == (
        "https://environment.example.test"
        if environment
        else "https://file.example.test"
    )


def test_legacy_openapi_callback_can_be_rebound(monkeypatch):
    from app.main import app, custom_openapi

    cached = app.openapi_schema
    monkeypatch.setattr(app, "openapi", custom_openapi)
    app.openapi_schema = None
    try:
        schema = app.openapi()
        assert schema["info"]["title"] == "TGO-Tech AI Service"
        assert "ErrorDetail" in schema["components"]["schemas"]
        assert app.openapi() is schema
    finally:
        app.openapi_schema = cached
