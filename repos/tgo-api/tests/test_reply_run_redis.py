"""Redis failures fail closed; opt-in tests use only fresh owned keys."""

import asyncio
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError

from app.core.config import settings
from app.schemas.ai_runs import ReplyRun
from app.schemas.reply_phase import ReplyPhaseIdentity
from app.services.run_registry import RedisRunRegistry, RegistryUnavailable

pytestmark = pytest.mark.asyncio


async def test_connection_failure_is_not_treated_as_missing_owner():
    registry = RedisRunRegistry("redis://fixture.invalid/0")
    registry._redis = AsyncMock()
    registry._redis.get.side_effect = ConnectionError("fixture")
    with pytest.raises(RegistryUnavailable):
        await registry.get("owned", "message")


async def test_invalid_or_wrong_owner_payload_fails_closed():
    item = ReplyRun(
        project_id="other",
        client_msg_no="message",
        channel_id="owned",
        channel_type=251,
    )
    for raw in ("not-json", "{}", item.model_dump_json()):
        with pytest.raises(RegistryUnavailable):
            RedisRunRegistry._decode(raw, "owned", "message")


@pytest.mark.skipif(
    os.getenv("TGO_REPLY_REDIS_TEST") != "1",
    reason="Requires explicitly enabled local Redis integration",
)
async def test_two_redis_workers_share_one_atomic_stop_publication_winner():
    assert settings.REDIS_URL, "Set the local Redis URL for the owned-key test"
    left, right = RedisRunRegistry(settings.REDIS_URL), RedisRunRegistry(
        settings.REDIS_URL
    )
    project = "reply-cancel-test-" + uuid4().hex
    owned = []
    try:
        for index in range(20):
            item = ReplyRun(
                project_id=project,
                client_msg_no=uuid4().hex,
                channel_id="fixture",
                channel_type=251,
            )
            await left.start(item)
            owned.append(item)
            phase = ReplyPhaseIdentity(
                project_id=project,
                client_msg_no=item.client_msg_no,
                generation=item.generation,
                phase_id=uuid4(),
                proof=uuid4(),
            )
            await left.start_phase(item, phase)
            assert (await right.get(project, item.client_msg_no)).phase == phase
            actions = [left.request_cancel(item), right.begin_publication(item)]
            if index % 2:
                actions.reverse()
            results = await asyncio.gather(*actions)
            assert {result.status for result in results} in (
                {"cancel_requested"},
                {"publishing"},
            )
            assert await left.get(project, item.client_msg_no) == await right.get(
                project, item.client_msg_no
            )
            await right.end_phase(phase)
            assert (await left.get(project, item.client_msg_no)).phase_ended
    finally:
        # Never enumerate or delete user keys: only keys created above.
        for item in owned:
            assert item.project_id == project and project.startswith(
                "reply-cancel-test-"
            )
            await left._redis.delete(left._key(project, item.client_msg_no))
        await left.close()
        await right.close()
