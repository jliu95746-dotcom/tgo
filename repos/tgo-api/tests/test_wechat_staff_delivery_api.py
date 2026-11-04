"""Bridge identity, scope and contract checks for human delivery endpoints."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.v1.endpoints import wechat_staff_deliveries as api
from app.schemas.wechat_staff_delivery import WeChatDeliveryRequest


@pytest.fixture
def identities(monkeypatch):
    platform = SimpleNamespace(id=uuid4(), project_id=uuid4(), type="wechat_local")
    visitor = SimpleNamespace(
        id=uuid4(), project_id=platform.project_id, platform_id=platform.id
    )
    body = WeChatDeliveryRequest(
        platform_api_key="test-only", channel_id=f"{visitor.id}-vtr"
    )
    db = SimpleNamespace(scalar=Mock(return_value=visitor))
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials="fixture-token"
    )
    monkeypatch.setattr(
        api, "validate_platform_and_project", Mock(return_value=(platform, object()))
    )
    scope = AsyncMock(return_value=(visitor, object()))
    monkeypatch.setattr(api, "authorize_bridge_visitor", scope)
    return db, platform, visitor, body, credentials, scope


@pytest.mark.asyncio
async def test_ready_requires_current_bridge_reception_scope(identities):
    db, platform, visitor, body, credentials, scope = identities
    assert await api.authorize(db, body, credentials) == (platform, visitor)
    scope.assert_awaited_once_with(db, platform, body.channel_id, 251, credentials)
    scope.side_effect = HTTPException(403, "Reception revoked")
    with pytest.raises(HTTPException) as raised:
        await api.authorize(db, body, credentials)
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_other_platform_type_and_other_visitor_are_rejected(identities):
    db, platform, visitor, body, credentials, scope = identities
    platform.type = "website"
    with pytest.raises(HTTPException) as raised:
        await api.authorize(db, body, credentials)
    assert raised.value.status_code == 403
    platform.type = "wechat_local"
    scope.return_value = (SimpleNamespace(id=uuid4()), object())
    with pytest.raises(HTTPException) as raised:
        await api.authorize(db, body, credentials)
    assert raised.value.status_code == 403
    db.scalar.return_value = None
    with pytest.raises(HTTPException) as raised:
        await api.authorize(db, body, credentials)
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_late_receipt_needs_original_identity_even_after_scope_revocation(
    identities, monkeypatch
):
    db, platform, visitor, body, credentials, scope = identities
    authenticate = Mock(return_value=visitor)
    monkeypatch.setattr(api, "authenticate_im_actor", authenticate)
    assert await api.authorize(db, body, credentials, check_scope=False) == (
        platform,
        visitor,
    )
    authenticate.assert_called_once_with(db, body.channel_id, credentials.credentials)
    scope.assert_not_awaited()
    with pytest.raises(HTTPException) as raised:
        await api.authorize(db, body, None, check_scope=False)
    assert raised.value.status_code == 401
    authenticate.return_value = SimpleNamespace(id=uuid4())
    with pytest.raises(HTTPException) as raised:
        await api.authorize(db, body, credentials, check_scope=False)
    assert raised.value.status_code == 403


def test_endpoint_contract_claim_requires_attempt_and_receipt_is_not_implied(
    client, monkeypatch
):
    platform, visitor = SimpleNamespace(id=uuid4()), object()
    monkeypatch.setattr(api, "authorize", AsyncMock(return_value=(platform, visitor)))
    monkeypatch.setattr(api.service, "ready", Mock(return_value=[]))
    claim_id = uuid4()
    claim = Mock(return_value=claim_id)
    monkeypatch.setattr(api.service, "claim", claim)
    confirm = AsyncMock()
    monkeypatch.setattr(api.service, "confirm", confirm)
    body = {
        "platform_api_key": "test-only",
        "channel_id": f"{uuid4()}-vtr",
        "channel_type": 251,
    }
    prefix = "/v1/chat/wechat-local-deliveries"
    assert client.post(f"{prefix}/ready", json=body).json() == {"replies": []}
    delivery_id, attempt = uuid4(), uuid4()
    assert client.post(f"{prefix}/{delivery_id}/claim", json=body).status_code == 422
    claim.assert_not_called()
    result = client.post(
        f"{prefix}/{delivery_id}/claim", json={**body, "attempt_id": str(attempt)}
    )
    assert result.status_code == 200 and result.json() == {"claim_id": str(claim_id)}
    confirm.assert_not_awaited()
    result = client.post(
        f"{prefix}/{delivery_id}/receipt",
        json={
            **body,
            "claim_id": str(claim_id),
            "result": "unknown",
            "receipt": None,
        },
    )
    assert result.status_code == 200 and result.json() == {"accepted": True}
    assert confirm.call_args.args[-2:] == ("unknown", None)
