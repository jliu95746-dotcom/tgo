"""Chat keeps the registered visitor and never creates one from an asserted ID."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models import Visitor
from app.services.im_access import issue_visitor_im_token
from tests.test_im_access import records as records


@pytest.mark.asyncio
async def test_website_chat_uses_registered_holder_even_when_user_field_is_absent(
    records,
):
    from app.services.visitor_chat_access import resolve_chat_visitor, check_chat_access

    db, _, _, platforms, visitors = records
    proof = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=issue_visitor_im_token(visitors[0], timedelta(hours=1)),
    )
    visitor, changed, access = await resolve_chat_visitor(
        db, platforms[0], None, proof, None
    )
    assert visitor.id == visitors[0].id and not changed
    assert db.query(Visitor).count() == 2
    assert proof.credentials not in repr(access) and proof.credentials not in str(
        access.model_dump()
    )
    check_chat_access(db, access)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["missing", "wrong_user", "other_visitor", "expired", "retired"]
)
async def test_website_chat_rejects_unowned_input_before_changing_records(
    records, fault
):
    from app.services.visitor_chat_access import resolve_chat_visitor

    db, _, _, platforms, visitors = records
    uid = visitors[0].platform_open_id
    token = issue_visitor_im_token(visitors[0], timedelta(hours=1))
    if fault == "wrong_user":
        uid = "asserted-other-user"
    elif fault == "other_visitor":
        token = issue_visitor_im_token(visitors[1], timedelta(hours=1))
    elif fault == "expired":
        token = issue_visitor_im_token(visitors[0], timedelta(seconds=-1))
    elif fault == "retired":
        visitors[0].access_revision += 1
        db.commit()
    proof = (
        None
        if fault == "missing"
        else HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    )
    with pytest.raises((TGOAPIException, HTTPException)):
        await resolve_chat_visitor(
            db, platforms[0], uid, proof, None, nickname="must-not-change"
        )
    assert db.query(Visitor).count() == 2
    assert visitors[0].nickname != "must-not-change"


@pytest.mark.asyncio
async def test_internal_service_token_cannot_replace_a_website_holder(
    records, monkeypatch
):
    from app.services.visitor_chat_access import resolve_chat_visitor

    db, _, _, platforms, visitors = records
    monkeypatch.setattr(
        settings, "SAAS_INTERNAL_TOKEN", SecretStr("owned-channel-service")
    )
    with pytest.raises((TGOAPIException, HTTPException)):
        await resolve_chat_visitor(
            db,
            platforms[0],
            visitors[0].platform_open_id,
            None,
            "owned-channel-service",
        )


@pytest.mark.asyncio
async def test_private_channel_retains_its_original_platform_and_service_identity(
    records, monkeypatch
):
    from app.services import visitor_chat_access as access_service

    db, _, _, platforms, visitors = records
    platforms[0].type = "wecom_kf"
    db.commit()
    monkeypatch.setattr(
        settings, "SAAS_INTERNAL_TOKEN", SecretStr("owned-channel-service")
    )
    create = AsyncMock(return_value=(visitors[0], False))
    monkeypatch.setattr(access_service, "get_or_create_channel_visitor", create)
    visitor, _, access = await access_service.resolve_chat_visitor(
        db, platforms[0], visitors[0].platform_open_id, None, "owned-channel-service"
    )
    assert visitor.id == visitors[0].id
    assert "owned-channel-service" not in repr(access)
    access_service.check_chat_access(db, access)
    platforms[0].api_key = "changed-key"
    db.commit()
    with pytest.raises((TGOAPIException, HTTPException)):
        access_service.check_chat_access(db, access)
    create.reset_mock()
    with pytest.raises((TGOAPIException, HTTPException)):
        await access_service.resolve_chat_visitor(
            db, platforms[0], visitors[0].platform_open_id, None, None
        )
    create.assert_not_awaited()
