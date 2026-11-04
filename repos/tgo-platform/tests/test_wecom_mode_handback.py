from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.domain.services.wecom_native import NativeBridge, NativeState


def bridge_fixture(state):
    bridge = NativeBridge(
        SimpleNamespace(id=uuid4(), cfg=SimpleNamespace(native_open_kfid="kf-test"))
    )
    cache = AsyncMock()
    lock = AsyncMock()
    lock.acquire.return_value = True
    cache.lock = Mock(return_value=lock)
    bridge._redis = AsyncMock(return_value=cache)
    bridge.state = AsyncMock(return_value=NativeState(service_state=state))
    bridge._transition = AsyncMock(return_value=NativeState(service_state=4))
    bridge._track = AsyncMock()
    bridge.mirror = AsyncMock()
    return bridge, cache, lock


@pytest.mark.asyncio
async def test_handback_ends_native_human_session_before_confirming_release():
    bridge, cache, lock = bridge_fixture(3)
    state = await bridge.handback("customer")
    assert state.service_state == 4
    bridge._transition.assert_awaited_once_with("customer", 4)
    cache.zrem.assert_awaited_with(bridge.prefix + ":queue", "customer")
    lock.release.assert_awaited_once()
    # The caller holds the visitor lock: calling API mirroring here deadlocks.
    bridge.mirror.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [0, 1, 4])
async def test_handback_preserves_an_already_api_managed_or_ended_session(state):
    bridge, _, _ = bridge_fixture(state)
    assert (await bridge.handback("customer")).service_state == state
    bridge._transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_waiting_pool_does_not_attempt_an_unsupported_state_transition():
    from app.domain.services.wecom_native import NativeQueueConflict

    bridge, _, lock = bridge_fixture(2)
    with pytest.raises(NativeQueueConflict):
        await bridge.handback("customer")
    bridge._transition.assert_not_awaited()
    lock.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_unconfirmed_provider_release_does_not_report_success():
    bridge, cache, lock = bridge_fixture(3)
    bridge._transition.side_effect = RuntimeError("provider unavailable")
    with pytest.raises(RuntimeError):
        await bridge.handback("customer")
    cache.zrem.assert_not_awaited()
    lock.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_busy_assignment_lock_does_not_change_provider_state():
    bridge, _, lock = bridge_fixture(3)
    lock.acquire.return_value = False
    with pytest.raises(RuntimeError):
        await bridge.handback("customer")
    bridge.state.assert_not_awaited()
    lock.release.assert_not_awaited()


def endpoint_fixture(monkeypatch):
    from app.api.v1 import wecom_native_mode as endpoint
    from app.api.v1.billing_receipts import require_service
    from app.db.base import get_db

    project, platform = uuid4(), uuid4()
    row = SimpleNamespace(
        id=platform,
        project_id=project,
        api_key="test-platform-key",
        config={"native_handoff_enabled": True, "native_open_kfid": "kf-test"},
    )
    db = AsyncMock()
    db.scalar.return_value = row
    bridge = AsyncMock()
    bridge.__aenter__.return_value = bridge
    bridge.handback.return_value = NativeState(service_state=4)
    monkeypatch.setattr(endpoint, "NativeBridge", Mock(return_value=bridge))
    app = FastAPI()
    app.include_router(endpoint.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_service] = lambda: None
    payload = {
        "project_id": str(project),
        "platform_id": str(platform),
        "external_userid": "customer",
    }
    return app, db, bridge, payload


@pytest.mark.asyncio
async def test_private_endpoint_checks_platform_scope_and_provider_receipt(monkeypatch):
    app, db, bridge, payload = endpoint_fixture(monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/wecom-native/handback", json=payload)
    assert response.status_code == 200
    assert response.json() == {"service_state": 4}
    query = str(db.scalar.call_args.args[0])
    assert "project_id" in query and "is_active" in query and "deleted_at" in query
    bridge.handback.assert_awaited_once_with("customer")


@pytest.mark.asyncio
async def test_unknown_platform_cannot_change_a_native_session(monkeypatch):
    app, db, bridge, payload = endpoint_fixture(monkeypatch)
    db.scalar.return_value = None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/wecom-native/handback", json=payload)
    assert response.status_code == 404
    bridge.handback.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_endpoint_requires_internal_credentials(monkeypatch):
    from app.api.v1.billing_receipts import require_service

    app, _, bridge, payload = endpoint_fixture(monkeypatch)
    app.dependency_overrides.pop(require_service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/wecom-native/handback", json=payload)
    assert response.status_code == 403
    bridge.handback.assert_not_awaited()
