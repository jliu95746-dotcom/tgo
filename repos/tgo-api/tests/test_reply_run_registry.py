"""Stop and publication must have one tenant-scoped atomic winner."""

import asyncio
from types import SimpleNamespace

import pytest

from app.schemas.ai_runs import ReplyRun
from app.services.run_registry import InMemoryRunRegistry, RegistryConflict
from app.services import run_registry as registry_module

pytestmark = pytest.mark.asyncio


def run(project="owned", message="same"):
    return ReplyRun(
        project_id=project,
        client_msg_no=message,
        channel_id="visitor-vtr",
        channel_type=251,
    )


async def test_project_and_generation_isolate_identical_message_numbers():
    registry = InMemoryRunRegistry()
    first, second = run(), run("other")
    await registry.start(first)
    await registry.start(second)
    assert (await registry.request_cancel(first)).status == "cancel_requested"
    assert (await registry.get("other", "same")).status == "active"
    assert await registry.finish(run(), "completed") is None
    assert (await registry.get("owned", "same")).status == "cancel_requested"


async def test_duplicate_cannot_overwrite_an_existing_owner():
    registry = InMemoryRunRegistry()
    first = run()
    await registry.start(first)
    with pytest.raises(RegistryConflict):
        await registry.start(run())
    assert (await registry.get("owned", "same")).generation == first.generation


async def test_stop_and_publication_race_never_both_win():
    for index in range(30):
        registry = InMemoryRunRegistry()
        item = run(message=str(index))
        await registry.start(item)
        actions = [registry.request_cancel(item), registry.begin_publication(item)]
        if index % 2:
            actions.reverse()
        outcomes = await asyncio.gather(*actions)
        assert {result.status for result in outcomes} in (
            {"cancel_requested"},
            {"publishing"},
        )


async def test_terminal_cannot_be_revived_or_replaced_by_late_cleanup():
    registry = InMemoryRunRegistry()
    item = run()
    await registry.start(item)
    await registry.request_cancel(item)
    assert (await registry.finish(item, "completed")).status == "cancel_requested"
    await registry.finish(item, "cancelled")
    for operation in (
        registry.heartbeat,
        registry.begin_publication,
        registry.request_cancel,
    ):
        assert (await operation(item)).status == "cancelled"
    assert (await registry.finish(item, "failed")).status == "cancelled"


async def test_missing_or_expired_owner_never_receives_permission_to_publish(
    monkeypatch,
):
    clock = [100.0]
    monkeypatch.setattr(
        registry_module, "time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    registry = InMemoryRunRegistry(ttl_seconds=0.001)
    item = run()
    assert await registry.begin_publication(item) is None
    await registry.start(item)
    clock[0] += 1
    assert await registry.begin_publication(item) is None
    assert await registry.request_cancel(item) is None
    assert await registry.get("owned", "same") is None
