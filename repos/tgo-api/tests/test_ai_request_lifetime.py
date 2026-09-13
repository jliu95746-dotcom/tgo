"""Internal run ownership is opt-in and survives the actual HTTP payload."""

import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest

from app.services import ai_client as client_module
from app.schemas.reply_phase import ReplyPhaseIdentity


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("with_phase", [False, True])
async def test_agent_client_sends_explicit_disconnect_ownership(
    monkeypatch, stream, owned, with_phase
):
    payloads = []

    async def respond(request):
        payloads.append(json.loads(request.content))
        if stream:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text=(
                    "event: event\ndata: "
                    '{"event_type":"workflow_completed","data":{}}\n\n'
                ),
            )
        response = httpx.Response(200, json={"content": "隔离测试"})
        response.elapsed = timedelta(0)
        return response

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    service = client_module.AIServiceClient()
    service.base_url = "http://owned.test"
    options = {"cancel_on_disconnect": True} if owned else {}
    phase = ReplyPhaseIdentity(
        project_id="fixture",
        client_msg_no=uuid4().hex,
        generation=uuid4(),
        phase_id=uuid4(),
        proof=uuid4(),
    )
    if with_phase:
        options["reply_phase"] = phase

    async def request():
        if stream:
            return [
                event
                async for event in service.run_supervisor_agent_stream(
                    message="隔离测试",
                    project_id="fixture",
                    **options,
                )
            ]
        return await service.run_supervisor_agent(
            message="隔离测试",
            project_id="fixture",
            **options,
        )

    if with_phase and not owned:
        with pytest.raises(ValueError):
            await request()
        assert not payloads
        return
    await request()
    assert payloads[0].get("cancel_on_disconnect", False) is owned
    assert payloads[0].get("reply_phase") == (
        phase.model_dump(mode="json") if with_phase else None
    )
    if not owned:
        assert "cancel_on_disconnect" not in payloads[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_agent_client_rejects_phase_for_another_project(stream):
    service = client_module.AIServiceClient()
    phase = ReplyPhaseIdentity(
        project_id="other",
        client_msg_no=uuid4().hex,
        generation=uuid4(),
        phase_id=uuid4(),
        proof=uuid4(),
    )
    with pytest.raises(ValueError):
        if stream:
            _ = [
                event
                async for event in service.run_supervisor_agent_stream(
                    message="隔离测试",
                    project_id="fixture",
                    cancel_on_disconnect=True,
                    reply_phase=phase,
                )
            ]
        else:
            await service.run_supervisor_agent(
                message="隔离测试",
                project_id="fixture",
                cancel_on_disconnect=True,
                reply_phase=phase,
            )
