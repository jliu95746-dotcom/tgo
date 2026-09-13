"""Only the current private phase capability can confirm ended AI work."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.schemas.ai_runs import ReplyRun
from app.schemas.reply_phase import ReplyPhaseIdentity
from app.services import ai_reply_control as control
from app.services.run_registry import InMemoryRunRegistry, RegistryConflict

pytestmark = pytest.mark.asyncio


def phase_for(item):
    return ReplyPhaseIdentity(
        project_id=item.project_id,
        client_msg_no=item.client_msg_no,
        generation=item.generation,
        phase_id=uuid4(),
        proof=uuid4(),
    )


async def start(registry):
    item = ReplyRun(
        project_id=str(uuid4()),
        client_msg_no=uuid4().hex,
        channel_id="fixture",
        channel_type=251,
    )
    await registry.start(item)
    phase = phase_for(item)
    await registry.start_phase(item, phase)
    return item, phase


@pytest.mark.parametrize(
    "field", ["project_id", "client_msg_no", "generation", "phase_id", "proof"]
)
async def test_wrong_identity_cannot_end_current_phase(field):
    registry = InMemoryRunRegistry()
    item, phase = await start(registry)
    value = str(uuid4()) if field in {"project_id", "client_msg_no"} else uuid4()
    wrong = phase.model_copy(update={field: value})
    assert await registry.end_phase(wrong) is None
    assert not (await registry.get(item.project_id, item.client_msg_no)).phase_ended
    result = await registry.end_phase(phase)
    assert result.phase_ended and result.status == "active"
    assert await registry.end_phase(phase) == result


async def test_unfinished_and_stale_phases_cannot_overwrite_new_owner():
    registry = InMemoryRunRegistry()
    item, old = await start(registry)
    new = phase_for(item)
    with pytest.raises(RegistryConflict):
        await registry.start_phase(item, new)
    await registry.end_phase(old)
    await registry.start_phase(item, new)
    assert await registry.end_phase(old) is None
    current = await registry.get(item.project_id, item.client_msg_no)
    assert current.phase == new and not current.phase_ended


async def test_stop_prevents_starting_another_model_phase():
    registry = InMemoryRunRegistry()
    item, phase = await start(registry)
    await registry.end_phase(phase)
    await registry.request_cancel(item)
    result = await registry.start_phase(item, phase_for(item))
    assert result.status == "cancel_requested" and result.phase == phase


@pytest.mark.parametrize("reason", ["upstream_stop_unconfirmed", "generation_failed"])
@pytest.mark.parametrize("receipt_first", [False, True])
async def test_late_receipt_only_resolves_a_stop_awaiting_confirmation(
    reason, receipt_first
):
    registry = InMemoryRunRegistry()
    item, phase = await start(registry)
    await registry.request_cancel(item)
    if receipt_first:
        await registry.end_phase(phase)
        result = await registry.finish(item, "failed", reason)
    else:
        await registry.finish(item, "failed", reason)
        result = await registry.end_phase(phase)
    assert result.phase_ended
    assert result.status == (
        "cancelled" if reason == "upstream_stop_unconfirmed" else "failed"
    )


async def test_receipt_route_is_private_and_rejects_forged_proof():
    from app.api.internal.router import internal_router
    from app.main import app as public_app

    item, phase = await start(control.run_registry)
    app = FastAPI()
    app.include_router(internal_router, prefix="/internal")
    assert not any("reply-phases" in route.path for route in public_app.routes)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned.test"
    ) as client:
        payload = {**phase.model_dump(mode="json"), "status": "ended"}
        forged = {**payload, "proof": str(uuid4())}
        assert (
            await client.post("/internal/ai/reply-phases/ended", json=forged)
        ).status_code == 404
        response = await client.post("/internal/ai/reply-phases/ended", json=payload)
    assert response.status_code == 200
    assert response.json() == {"accepted": True, "phase_id": str(phase.phase_id)}
    assert (
        await control.run_registry.get(item.project_id, item.client_msg_no)
    ).phase_ended
