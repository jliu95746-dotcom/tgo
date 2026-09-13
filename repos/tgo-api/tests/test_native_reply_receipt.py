"""Opt-in native callback check using only fresh Redis records and no model."""

import asyncio
import os
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import pytest

from app.core.config import settings
from app.schemas.ai_runs import ReplyRun
from app.schemas.reply_phase import ReplyPhaseIdentity
from app.services.run_registry import RedisRunRegistry

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("TGO_REPLY_NATIVE_TEST") != "1",
        reason="Requires explicitly enabled native AI and private API services",
    ),
]


@pytest.mark.parametrize("stream", [False, True])
async def test_native_ai_reports_end_to_private_api_and_shared_redis(
    stream: bool,
) -> None:
    # Never run this integration against a remote deployment or real agent.
    assert settings.REDIS_URL
    for address in (settings.REDIS_URL, settings.AI_SERVICE_URL):
        assert urlparse(address).hostname in {"127.0.0.1", "localhost"}
    registry = RedisRunRegistry(settings.REDIS_URL)
    project, absent_agent = uuid4(), uuid4()
    client_no = "native-receipt-test-" + uuid4().hex
    item = ReplyRun(
        project_id=str(project),
        client_msg_no=client_no,
        channel_id="isolated-native-test",
        channel_type=251,
    )
    phase = ReplyPhaseIdentity(
        project_id=item.project_id,
        client_msg_no=client_no,
        generation=item.generation,
        phase_id=uuid4(),
        proof=uuid4(),
    )
    started = False
    try:
        await registry.start(item)
        started = True
        await registry.start_phase(item, phase)
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.post(
                settings.AI_SERVICE_URL.rstrip("/") + "/api/v1/agents/run",
                params={"project_id": str(project)},
                json={
                    # The absent agent fails resolution before model/tool execution.
                    "agent_id": str(absent_agent),
                    "message": "隔离回执校验",
                    "stream": stream,
                    "cancel_on_disconnect": True,
                    "disable_tools": True,
                    "enable_memory": False,
                    "reply_phase": phase.model_dump(mode="json"),
                },
            )
        assert response.status_code == 200
        if stream:
            assert "workflow_failed" in response.text
            assert "agent_execution_started" not in response.text
        else:
            assert response.json()["success"] is False
        async with asyncio.timeout(5):
            while True:
                recorded = await registry.get(item.project_id, client_no)
                assert recorded is not None and recorded.phase == phase
                if recorded.phase_ended:
                    break
                await asyncio.sleep(0.05)
        assert recorded.status == "active"
    finally:
        if started:
            assert client_no.startswith("native-receipt-test-")
            # Delete only the exact transient test record created above.
            await registry._redis.delete(registry._key(item.project_id, client_no))
        await registry.close()
