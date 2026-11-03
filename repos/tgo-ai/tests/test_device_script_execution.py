"""AI calls keep one trusted session, immutable input and receipt identity."""

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from jose import jwt

from app.config import settings
from app.exceptions import ExternalServiceError
from app.schemas.device_script_policy import (
    ScriptDescriptor,
    ScriptPolicy,
    ScriptParameter,
    script_policy_digest,
)
from app.schemas.device_script_execution import ScriptPolicyRead
from app.services import device_execution as lifecycle
from app.services import device_control_client as transport
from app.services.device_script_execution import ApprovedScriptExecution


@pytest.fixture
def setup(monkeypatch):
    context = SimpleNamespace(
        project_id=str(uuid4()),
        agent=SimpleNamespace(
            id=uuid4(), name="隔离脚本测试", bound_device_id=str(uuid4())
        ),
        disable_tools=False,
        response_purpose="standard",
        parent_workflow_execution_id=uuid4(),
    )
    fake = SimpleNamespace(
        start_session=AsyncMock(),
        heartbeat_session=AsyncMock(),
        finish_session=AsyncMock(),
    )
    monkeypatch.setattr(lifecycle, "device_control_client", fake)
    descriptor = ScriptDescriptor(
        script_id="daily_report",
        title="已审核报表",
        version="v1",
        grant_digest="a" * 64,
        effect="read_only",
        timeout_seconds=20,
        max_output_bytes=4096,
        max_runs=2,
        parameters=[
            {
                "name": "count",
                "kind": "integer",
                "required": True,
                "minimum": 1,
                "maximum": 10,
            }
        ],
        result_fields=[
            {
                "name": "total",
                "kind": "integer",
                "required": True,
                "minimum": 0,
                "maximum": 100,
            }
        ],
    )
    policy = ScriptPolicy.model_construct(
        protocol_version=1,
        revision=3,
        script_id="daily_report",
        grant_digest="a" * 64,
        timeout_seconds=20,
        max_output_bytes=4096,
        max_runs=2,
        digest="",
    )
    policy.digest = script_policy_digest(policy)
    bound = ScriptPolicyRead(
        device_id=context.agent.bound_device_id,
        project_id=context.project_id,
        policy=policy,
        status="active",
        updated_at=datetime.now(timezone.utc),
    )
    return context, descriptor, bound


def connect(monkeypatch, handler):
    original = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return original(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(transport.httpx, "AsyncClient", client)


def receipt(call, **changes):
    body = {
        "device_id": str(call.identity.device_id),
        "execution_id": str(call.execution_id),
        "session_id": str(call.identity.session_id),
        "agent_id": str(call.identity.agent_id),
        "workflow_execution_id": str(call.identity.workflow_execution_id),
        "policy_digest": call.policy_digest,
        "parameters_digest": call.parameters_digest,
        "context_digest": call.context_digest,
        "status": "succeeded",
        "failure": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "result_available": True,
        "fields": {"total": 3},
    }
    body.update(changes)
    return body


async def test_signed_run_and_repeated_call_only_query(setup, monkeypatch):
    context, descriptor, bound = setup
    requests = []
    async with lifecycle.track_device_execution(context):
        values = {"count": 2}
        run = ApprovedScriptExecution.prepare(
            bound, descriptor, values, execution_id=uuid4()
        )
        values["count"] = 9
        descriptor.result_fields.clear()
        bound.policy.timeout_seconds = 1

        async def handle(request):
            requests.append(request)
            assert request.url.path == (
                f"/v1/devices/{run.call.identity.device_id}"
                f"/script-executions/{run.call.execution_id}"
            )
            claims = jwt.decode(
                request.headers["Authorization"][7:],
                settings.secret_key,
                algorithms=["HS256"],
                audience="tgo-device-control",
                issuer="tgo-internal",
            )
            assert claims["sub"] == "tgo-ai"
            for name in ("project_id", "device_id", "session_id"):
                assert claims[name] == str(getattr(run.call.identity, name))
            assert claims["script_policy_digest"] == run.call.policy_digest
            assert "read_policy_digest" not in claims
            if request.method == "POST":
                assert json.loads(request.content) == {
                    "parameters": {"count": 2}
                }
            else:
                assert request.content == b""
            return httpx.Response(200, json=receipt(run.call))

        connect(monkeypatch, handle)
        assert (await run.run()).fields == {"total": 3}
        assert (await run.run()).fields == {"total": 3}
        assert [request.method for request in requests] == ["POST", "GET"]
    with pytest.raises(ExternalServiceError):
        await run.read()
    assert len(requests) == 2


@pytest.mark.parametrize("failure", ["timeout", "cancel", "invalid"])
async def test_unknown_dispatch_never_posts_again(setup, monkeypatch, failure):
    context, descriptor, bound = setup
    requests = []
    async with lifecycle.track_device_execution(context):
        run = ApprovedScriptExecution.prepare(
            bound, descriptor, {"count": 1}, execution_id=uuid4()
        )

        async def handle(request):
            requests.append(request.method)
            if len(requests) == 1:
                if failure == "cancel":
                    raise asyncio.CancelledError()
                if failure == "timeout":
                    raise httpx.ReadTimeout(
                        "private business input", request=request
                    )
                return httpx.Response(200, text="private invalid response")
            return httpx.Response(200, json=receipt(run.call))

        connect(monkeypatch, handle)
        expected = (
            asyncio.CancelledError
            if failure == "cancel"
            else ExternalServiceError
        )
        with pytest.raises(expected) as error:
            await run.run()
        assert "private" not in str(error.value)
        await run.run()
        assert requests == ["POST", "GET"]


@pytest.mark.parametrize(
    "field",
    [
        "device_id",
        "execution_id",
        "session_id",
        "agent_id",
        "workflow_execution_id",
        "policy_digest",
        "parameters_digest",
        "context_digest",
        "extra",
        "fields",
        "result_available",
    ],
)
async def test_foreign_or_unreviewed_receipt_rejected(
    setup, monkeypatch, field
):
    context, descriptor, bound = setup
    async with lifecycle.track_device_execution(context):
        run = ApprovedScriptExecution.prepare(
            bound, descriptor, {"count": 1}, execution_id=uuid4()
        )
        changes = {field: str(uuid4())}
        if field.endswith("digest"):
            changes[field] = "b" * 64
        elif field == "fields":
            changes[field] = {"total": True, "private": "private data"}
        elif field == "result_available":
            changes[field] = "true"

        async def handle(request):
            return httpx.Response(200, json=receipt(run.call, **changes))

        connect(monkeypatch, handle)
        with pytest.raises(ExternalServiceError) as error:
            await run.run()
        assert "private" not in str(error.value)


@pytest.mark.parametrize(
    "case",
    [
        "untracked",
        "project",
        "device",
        "pending",
        "revoked",
        "write",
        "grant",
        "script",
        "limit",
        "boolean",
        "extra_input",
        "missing",
    ],
)
async def test_invalid_scope_or_input_never_contacts_control(
    setup, monkeypatch, case
):
    context, descriptor, bound = setup
    parameters = {"count": 1}
    if case in {"project", "device"}:
        setattr(bound, case + "_id", uuid4())
    elif case in {"pending", "revoked"}:
        bound.status = case
    elif case == "write":
        descriptor.effect = "write"
    elif case == "grant":
        descriptor.grant_digest = "b" * 64
    elif case == "script":
        descriptor.script_id = "other_report"
    elif case == "limit":
        descriptor.timeout_seconds = 1
    elif case == "boolean":
        parameters["count"] = True
    elif case == "extra_input":
        parameters["command"] = "do not execute"
    elif case == "missing":
        parameters.clear()

    def unexpected(**kwargs):
        pytest.fail("Invalid input must not create a transport")

    monkeypatch.setattr(transport.httpx, "AsyncClient", unexpected)
    if case == "untracked":
        with pytest.raises(ExternalServiceError):
            ApprovedScriptExecution.prepare(bound, descriptor, parameters)
    else:
        async with lifecycle.track_device_execution(context):
            with pytest.raises(ExternalServiceError):
                ApprovedScriptExecution.prepare(bound, descriptor, parameters)


async def test_concurrent_retry_waits_for_first_dispatch(setup, monkeypatch):
    context, descriptor, bound = setup
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []
    async with lifecycle.track_device_execution(context):
        run = ApprovedScriptExecution.prepare(bound, descriptor, {"count": 1})

        async def handle(request):
            requests.append(request.method)
            if request.method == "POST":
                entered.set()
                await release.wait()
            return httpx.Response(200, json=receipt(run.call))

        connect(monkeypatch, handle)
        first = asyncio.create_task(run.run())
        await entered.wait()
        second = asyncio.create_task(run.run())
        await asyncio.sleep(0)
        assert requests == ["POST"]
        release.set()
        await asyncio.gather(first, second)
        assert requests == ["POST", "GET"]


async def test_context_digest_matches_control_formula(setup):
    context, descriptor, bound = setup
    async with lifecycle.track_device_execution(context):
        run = ApprovedScriptExecution.prepare(bound, descriptor, {"count": 1})
        identity = run.call.identity
        text = (
            f"tgo-approved-script-context-v1\n{identity.project_id}\n"
            f"{identity.device_id}\n{identity.session_id}\n"
            f"{identity.agent_id}\n"
            f"{identity.workflow_execution_id}\n{run.call.policy_digest}\n"
        )
        assert (
            run.call.context_digest
            == hashlib.sha256(text.encode()).hexdigest()
        )


async def test_scope_lost_during_request_hides_late_result(setup, monkeypatch):
    context, descriptor, bound = setup
    async with lifecycle.track_device_execution(context) as execution:
        run = ApprovedScriptExecution.prepare(bound, descriptor, {"count": 1})

        async def handle(request):
            execution.closed = True
            return httpx.Response(200, json=receipt(run.call))

        connect(monkeypatch, handle)
        with pytest.raises(ExternalServiceError):
            await run.run()


async def test_recovery_read_cannot_turn_into_a_dispatch(setup, monkeypatch):
    context, descriptor, bound = setup
    requests = []
    async with lifecycle.track_device_execution(context):
        run = ApprovedScriptExecution.prepare(bound, descriptor, {"count": 1})

        async def handle(request):
            requests.append(request.method)
            return httpx.Response(200, json=receipt(run.call))

        connect(monkeypatch, handle)
        await run.read()
        await run.run()
        assert requests == ["GET", "GET"]


async def test_invalid_execution_id_never_enters_a_transport(setup):
    context, descriptor, bound = setup
    async with lifecycle.track_device_execution(context):
        with pytest.raises(ExternalServiceError):
            ApprovedScriptExecution.prepare(
                bound, descriptor, {"count": 1}, execution_id="../foreign"
            )


@pytest.mark.parametrize("status", [307, 403, 404, 409, 422, 503])
async def test_transport_error_is_sanitized_and_never_retried(
    setup, monkeypatch, status
):
    context, descriptor, bound = setup
    requests = []
    async with lifecycle.track_device_execution(context):
        run = ApprovedScriptExecution.prepare(bound, descriptor, {"count": 1})

        async def handle(request):
            requests.append(request.method)
            return httpx.Response(
                status,
                text="private upstream business values",
                headers={"location": "https://invalid.example"},
            )

        connect(monkeypatch, handle)
        with pytest.raises(ExternalServiceError) as error:
            await run.run()
        assert "private" not in str(error.value)
        assert requests == ["POST"]


async def test_result_budget_counts_utf8_bytes(setup, monkeypatch):
    context, descriptor, bound = setup
    descriptor.result_fields = [
        ScriptParameter(
            **{
                "name": "summary",
                "kind": "string",
                "required": True,
                "max_length": 4000,
            }
        )
    ]
    descriptor = ScriptDescriptor.model_validate(descriptor.model_dump())
    bound.policy.max_output_bytes = 256
    bound.policy.digest = script_policy_digest(bound.policy)
    async with lifecycle.track_device_execution(context):
        run = ApprovedScriptExecution.prepare(bound, descriptor, {"count": 1})

        async def handle(request):
            return httpx.Response(
                200, json=receipt(run.call, fields={"summary": "测" * 100})
            )

        connect(monkeypatch, handle)
        with pytest.raises(ExternalServiceError):
            await run.run()


async def test_success_metadata_does_not_invent_a_business_result(
    setup, monkeypatch
):
    context, descriptor, bound = setup
    async with lifecycle.track_device_execution(context):
        run = ApprovedScriptExecution.prepare(bound, descriptor, {"count": 1})

        async def handle(request):
            return httpx.Response(
                200, json=receipt(run.call, result_available=False, fields={})
            )

        connect(monkeypatch, handle)
        result = await run.run()
        assert result.status == "succeeded"
        assert not result.result_available and not result.fields


@pytest.mark.parametrize(
    "case", ["missing_session", "mixed_modes", "bad_digest"]
)
def test_script_credentials_require_one_valid_session(case):
    arguments = {"script_policy_digest": "a" * 64, "session_id": uuid4()}
    if case == "missing_session":
        arguments["session_id"] = None
    elif case == "mixed_modes":
        arguments["read_policy_digest"] = "b" * 64
    else:
        arguments["script_policy_digest"] = "not-a-digest"
    with pytest.raises(ValueError):
        transport.DeviceControlClient.headers(uuid4(), uuid4(), **arguments)
