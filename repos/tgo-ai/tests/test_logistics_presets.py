"""Official provider wire contracts, using isolated HTTP transports only."""
import base64
import hashlib
import json
from unittest.mock import patch
from uuid import uuid4
from urllib.parse import parse_qs

import httpx
import pytest

from app.config import settings
from app.services.logistics_provider import (
    execute_logistics_provider,
    prepare_config,
    public_config,
)


def provider(kind: str) -> dict[str, object]:
    return {
        "provider_kind": kind,
        "provider_name": "ignored",
        "account_id": "fixture-account",
        "credential": "fixture-key",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["kuaidi100", "kdniao"])
async def test_saved_preset_executes_through_archive_and_agent_paths(
    db_session, monkeypatch, kind
):
    from app.api.v1.tools import create_tool, update_tool
    from app.schemas.tool import ToolCreate, ToolUpdate
    from app.models.tool import Tool, ToolType
    from app.services.tool_executor import ToolExecutor
    from app.runtime.tools.utils import create_http_tool

    monkeypatch.setattr(settings, "secret_key", "fixture-master-key")
    project_id = uuid4()
    saved = await create_tool(
        ToolCreate(
            project_id=project_id,
            name="express_service",
            tool_type=ToolType.FUNCTION,
            endpoint="",
            config={"logistics_provider": provider(kind)},
        ),
        db_session,
    )
    public = saved.model_dump(mode="json")
    assert "fixture-key" not in json.dumps(public)
    assert "credential_encrypted" not in json.dumps(public)
    assert saved.endpoint.startswith("https://")
    await update_tool(
        saved.id, ToolUpdate(config=public["config"]), project_id, db_session
    )
    record = await db_session.get(Tool, saved.id)
    config = record.config["logistics_provider"]
    executor = ToolExecutor(db_session, project_id)
    await executor.register_tools(tool_ids=[saved.id])
    agent_tool = create_http_tool(
        "express_service",
        "查询",
        saved.endpoint,
        parameters=record.config["parameters"],
        logistics_provider=config,
    )
    calls = []

    async def respond(client, endpoint, config, credential, tracking_no, args):
        calls.append(args)
        assert credential == "fixture-key"
        if kind == "kuaidi100":
            return {
                "status": "200",
                "nu": tracking_no,
                "data": [{"time": "2026-09-13 09:00:00", "context": "运输中"}],
            }
        return {
            "Success": True,
            "LogisticCode": tracking_no,
            "Traces": [{"AcceptTime": "2026-09-13 09:00:00", "AcceptStation": "运输中"}],
        }

    args = {"tracking_no": "SF1234567890", "phone": "1234"}
    with patch("app.services.logistics_provider.query_preset", side_effect=respond):
        archive = json.loads(await executor.execute("express_service", args))
        agent = json.loads(await agent_tool.entrypoint(**args))
    assert archive == agent
    assert len(archive["events"]) == 1
    assert calls == [args, args]


@pytest.mark.asyncio
async def test_kdniao_signed_auto_query_and_mapping() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode())
        payload = body["RequestData"][0]
        digest = hashlib.md5((payload + "fixture-key").encode()).hexdigest()
        assert body["DataSign"][0] == base64.b64encode(digest.encode()).decode()
        assert body["EBusinessID"] == ["fixture-account"]
        assert body["RequestType"] == ["8002"]
        assert json.loads(payload)["LogisticCode"] == "SF1234567890"
        assert "fixture-key" not in str(request.url)
        return httpx.Response(
            200,
            json={
                "Success": True,
                "LogisticCode": "SF1234567890",
                "ShipperCode": "SF",
                "Traces": [
                    {"AcceptTime": "2026-09-13 09:00:00", "AcceptStation": "运输中"}
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await execute_logistics_provider(
            settings.logistics_kdniao_endpoint,
            provider("kdniao"),
            {"tracking_no": "SF1234567890"},
            client=client,
        )
    assert result["events"][0]["description"] == "运输中"


@pytest.mark.asyncio
async def test_kuaidi100_recognition_then_signed_query() -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = parse_qs(request.content.decode())
        if request.url.path.endswith("/auto"):
            assert body["key"] == ["fixture-key"]
            return httpx.Response(200, json=[{"comCode": "yuantong", "name": "圆通速递"}])
        param = body["param"][0]
        assert json.loads(param)["com"] == "yuantong"
        assert (
            body["sign"][0]
            == hashlib.md5((param + "fixture-keyfixture-account").encode())
            .hexdigest()
            .upper()
        )
        return httpx.Response(
            200,
            json={
                "status": "200",
                "com": "yuantong",
                "nu": "YT1234567890",
                "data": [{"time": "2026-09-13 09:00:00", "context": "已揽收"}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await execute_logistics_provider(
            settings.logistics_kuaidi100_endpoint,
            provider("kuaidi100"),
            {"tracking_no": "YT1234567890"},
            client=client,
        )
    assert len(calls) == 2
    assert result["events"][0]["description"] == "已揽收"


def test_preset_is_server_owned_and_cannot_reuse_key_across_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "secret_key", "fixture-encryption-key")
    saved = prepare_config(
        provider("kdniao"), None, endpoint="https://untrusted.example/query"
    )
    assert saved["credential_endpoint"] == settings.logistics_kdniao_endpoint
    assert saved["provider_name"] == "快递鸟"
    changed = {**public_config(saved), "account_id": "other-account"}
    with pytest.raises(ValueError):
        prepare_config(changed, saved, endpoint=settings.logistics_kdniao_endpoint)


@pytest.mark.asyncio
async def test_ambiguous_carrier_does_not_guess_or_bill_query() -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=[{"comCode": "a"}, {"comCode": "b"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError):
            await execute_logistics_provider(
                settings.logistics_kuaidi100_endpoint,
                provider("kuaidi100"),
                {"tracking_no": "1234567890"},
                client=client,
            )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_business_failure_cannot_become_success() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"Success": False, "Reason": "fixture-private-error"}
            )
        )
    ) as client:
        with pytest.raises(ValueError):
            await execute_logistics_provider(
                settings.logistics_kdniao_endpoint,
                provider("kdniao"),
                {"tracking_no": "1234567890"},
                client=client,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["kuaidi100", "kdniao"])
async def test_explicit_carrier_and_phone_use_one_query(kind: str) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = parse_qs(request.content.decode())
        if kind == "kuaidi100":
            payload = json.loads(body["param"][0])
            assert payload["com"] == "shunfeng"
            assert payload["phone"] == "1234"
            return httpx.Response(
                200,
                json={
                    "status": "200",
                    "nu": "SF1234567890",
                    "data": [{"time": "2026-09-13 09:00:00", "context": "运输中"}],
                },
            )
        payload = json.loads(body["RequestData"][0])
        assert payload["ShipperCode"] == "SF"
        assert payload["CustomerName"] == "1234"
        return httpx.Response(
            200,
            json={
                "Success": True,
                "LogisticCode": "SF1234567890",
                "Traces": [
                    {"AcceptTime": "2026-09-13 09:00:00", "AcceptStation": "运输中"}
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await execute_logistics_provider(
            "",
            provider(kind),
            {
                "tracking_no": "SF1234567890",
                "carrier_code": "shunfeng" if kind == "kuaidi100" else "SF",
                "phone": "1234",
            },
            client=client,
        )
    assert len(calls) == 1
