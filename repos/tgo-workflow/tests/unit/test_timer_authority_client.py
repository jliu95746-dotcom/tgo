"""Timer permission RPC must fail closed on denial or a mismatched echo."""

from datetime import datetime, timezone
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from pydantic import SecretStr
import pytest

from app.config import settings
from app.integrations import timer_authority_client as authority
from app.schemas.timer_authority import TimerAuthority


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_kind",
    ["valid", "revoked", "unavailable", "wrong_scope", "malformed"],
)
async def test_private_permission_response_is_bound_to_the_requested_source(
    monkeypatch, response_kind
):
    query = TimerAuthority(
        project_id=uuid4(),
        actor_staff_id=uuid4(),
        workflow_id="owned",
        schedule_id=uuid4(),
        revision=1,
        occurrence_id=uuid4(),
        scheduled_at=datetime.now(timezone.utc),
        release_id="published",
        release_digest="a" * 64,
    )
    payload = query.model_dump(mode="json")
    if response_kind == "wrong_scope":
        payload["occurrence_id"] = str(uuid4())
    if response_kind == "malformed":
        payload = {"private": "invalid"}
    code = (
        409
        if response_kind == "revoked"
        else 503
        if response_kind == "unavailable"
        else 200
    )
    client = AsyncMock()
    client.post.return_value = httpx.Response(code, json=payload)

    @asynccontextmanager
    async def private_client():
        yield client

    monkeypatch.setattr(
        authority.HttpClient, "internal_client", private_client
    )
    monkeypatch.setattr(
        settings, "SECRET_KEY", SecretStr("synthetic-timer-check-only")
    )
    if response_kind == "valid":
        await authority.verify_timer_authority(query)
    else:
        error = (
            PermissionError
            if response_kind in {"revoked", "wrong_scope"}
            else RuntimeError
        )
        with pytest.raises(error):
            await authority.verify_timer_authority(query)
    assert client.post.await_args.kwargs["json"]["occurrence_id"] == str(
        query.occurrence_id
    )
