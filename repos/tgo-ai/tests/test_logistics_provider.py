"""Provider mapping and both execution entrances must use the same adapter."""
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.schemas.logistics_provider import LogisticsProviderConfig
from app.services.logistics_provider import (
    execute_logistics_provider,
    prepare_config,
    public_config,
)


def config(**overrides: object) -> dict[str, object]:
    return {
        "provider_name": "测试服务商",
        "method": "GET",
        "tracking_param": "no",
        "auth_type": "appcode",
        "credential": "test-only-key",
        "success_path": "code",
        "success_value": "0",
        "events_path": "result.list",
        "time_field": "date",
        "description_field": "text",
        **overrides,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,body_format", [("GET", "json"), ("POST", "json"), ("POST", "form")]
)
async def test_request_and_response_mapping(method: str, body_format: str) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "result": {"list": [{"date": "2026-09-13 09:00:00", "text": "运输中"}]},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await execute_logistics_provider(
            "https://provider.example/query",
            config(method=method, body_format=body_format),
            {"tracking_no": "SF1234567890", "credential": "injected"},
            client=client,
        )
    assert result["events"][0]["description"] == "运输中"
    assert calls[0].headers["Authorization"] == "APPCODE test-only-key"
    if method == "GET":
        assert calls[0].url.params["no"] == "SF1234567890"
    else:
        assert b"SF1234567890" in calls[0].content
    assert "injected" not in str(calls[0].url) + calls[0].content.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"code": 401},
        {"code": 0, "result": {"list": []}},
        {"code": 0, "result": {"list": [{"text": "运输中"}]}},
    ],
)
async def test_no_false_success(payload: dict[str, object]) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ValueError):
            await execute_logistics_provider(
                "https://provider.example/query",
                config(),
                {"tracking_no": "SF1234567890"},
                client=client,
            )


def test_secret_is_encrypted_redacted_and_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "secret_key", "test-only-encryption-master-key")
    saved = prepare_config(config(), None, endpoint="https://provider.example/query")
    assert "test-only-key" not in json.dumps(saved)
    visible = public_config(saved)
    assert "credential_encrypted" not in visible
    assert visible["credential_configured"] is True
    assert (
        prepare_config(visible, saved, endpoint="https://provider.example/query")[
            "credential_encrypted"
        ]
        == saved["credential_encrypted"]
    )


def test_no_credential_reuse_after_endpoint_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "secret_key", "test-only-encryption-master-key")
    saved = prepare_config(config(), None, endpoint="https://provider.example/query")
    with pytest.raises(ValueError):
        prepare_config(
            public_config(saved), saved, endpoint="https://different.example/query"
        )


def test_invalid_parameter_rejected() -> None:
    with pytest.raises(ValueError):
        LogisticsProviderConfig.model_validate(config(tracking_param=""))


@pytest.mark.asyncio
async def test_agent_http_wrapper_calls_shared_adapter() -> None:
    from app.runtime.tools.utils import create_http_tool

    with patch(
        "app.services.logistics_provider.execute_logistics_provider",
        new_callable=AsyncMock,
        return_value={"events": []},
    ) as execute:
        tool = create_http_tool(
            "express_service",
            "查询",
            "https://provider.example/query",
            logistics_provider=config(),
        )
        await tool.entrypoint(tracking_no="SF1234567890")
        execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_migrate_store_record_keep_id_and_execute_directly(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4
    from app.config import settings
    from app.api.v1.tools import create_tool, update_tool
    from app.schemas.tool import ToolCreate, ToolUpdate
    from app.models.tool import Tool, ToolType, ToolSourceType
    from app.services.tool_executor import ToolExecutor

    monkeypatch.setattr(settings, "secret_key", "test-only-encryption-master-key")
    project_id = uuid4()
    legacy = await create_tool(
        ToolCreate(
            project_id=project_id,
            name="express_service",
            tool_type=ToolType.MCP,
            tool_source_type=ToolSourceType.STORE,
            store_resource_id="old-resource",
            endpoint="https://legacy.example/mcp",
        ),
        db_session,
    )
    changed = await update_tool(
        legacy.id,
        ToolUpdate(
            endpoint="https://provider.example/query",
            config={"logistics_provider": config()},
        ),
        project_id,
        db_session,
    )
    visible = changed.model_dump(mode="json")
    assert changed.id == legacy.id
    assert changed.tool_source_type == ToolSourceType.LOCAL
    assert changed.store_resource_id is None
    assert "test-only-key" not in json.dumps(visible)
    assert "credential_encrypted" not in json.dumps(visible)
    persisted = await db_session.get(Tool, legacy.id)
    assert persisted.config["logistics_provider"]["credential_encrypted"]
    # Leaving the key blank preserves it on a real PATCH, including the endpoint.
    await update_tool(
        legacy.id, ToolUpdate(config=visible["config"]), project_id, db_session
    )
    executor = ToolExecutor(db_session, project_id)
    await executor.register_tools(tool_ids=[legacy.id])
    with patch(
        "app.services.logistics_provider.execute_logistics_provider",
        new_callable=AsyncMock,
        return_value={
            "events": [{"time": "2026-09-13 09:00:00", "description": "运输中"}]
        },
    ) as execute:
        result = await executor.execute(
            "express_service", {"tracking_no": "SF1234567890"}
        )
        assert "events" in json.loads(result)
        execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_failure_is_not_success_and_does_not_leak_key() -> None:
    from app.runtime.tools.utils import create_http_tool

    tool = create_http_tool(
        "express_service",
        "查询",
        "https://provider.example/query",
        logistics_provider=config(),
    )
    failure = httpx.HTTPStatusError(
        "secret-in-upstream-body",
        request=httpx.Request("GET", "https://provider.example/query"),
        response=httpx.Response(403),
    )
    with patch(
        "app.services.logistics_provider.execute_logistics_provider",
        new_callable=AsyncMock,
        side_effect=failure,
    ):
        result = await tool.entrypoint(tracking_no="SF1234567890")
    assert "403" in result
    assert "secret-in-upstream-body" not in result
