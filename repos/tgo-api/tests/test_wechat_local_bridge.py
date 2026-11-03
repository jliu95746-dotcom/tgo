"""Personal WeChat authorization is tied to the signed, canonical visitor."""
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.models import Platform, Project, Visitor
from app.services.im_access import issue_visitor_im_token
from app.services import wechat_local_bridge as service
from app.services.wechat_local_bridge import (
    WeChatLocalScope, authorize_bridge_visitor, bridge_status, publish_bridge_scope, require_bridge_visitor,
)
from app.utils.wechat_local import is_filehelper_identity, is_wechat_local_source


@compiles(JSONB, "sqlite")
def compile_jsonb(_element, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def bridge():
    engine = create_engine("sqlite://")
    for model in (Project, Platform, Visitor):
        model.__table__.create(engine)
    with Session(engine) as db:
        project = Project(id=uuid4(), name="bridge fixture", api_key="fixture-project-only")
        platform = Platform(id=uuid4(), project_id=project.id, name="bridge",
                            type="wechat_local", api_key="fixture-only", is_active=True)
        visitor = Visitor(id=uuid4(), project_id=project.id, platform_id=platform.id,
                          platform_open_id="wechat-local:0123456789abcdef:filehelper")
        db.add_all([project, platform, visitor])
        db.commit()
        yield db, platform, visitor
    engine.dispose()


def test_signed_scope_and_canonical_channel(bridge):
    db, platform, visitor = bridge
    channel = f"{visitor.id}-vtr"
    token = issue_visitor_im_token(visitor, timedelta(minutes=1))
    proof = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert require_bridge_visitor(db, platform, channel, 251, proof).id == visitor.id
    for supplied_channel, supplied_type, supplied_proof in (
        (str(visitor.id), 251, proof),
        (channel, 1, proof),
        (channel, 251, None),
    ):
        with pytest.raises(HTTPException) as denied:
            require_bridge_visitor(db, platform, supplied_channel, supplied_type, supplied_proof)
        assert denied.value.status_code == 401
    wrong_platform = Platform(id=uuid4(), project_id=platform.project_id)
    with pytest.raises(HTTPException) as denied:
        require_bridge_visitor(db, wrong_platform, channel, 251, proof)
    assert denied.value.status_code == 403
    visitor.platform_open_id = "wechat-local:0123456789abcdef:friend"
    db.commit()
    with pytest.raises(HTTPException) as denied:
        require_bridge_visitor(db, platform, channel, 251, proof)
    assert denied.value.status_code == 403


def test_filehelper_identity_and_source_format():
    assert is_filehelper_identity("wechat-local:0123456789abcdef:filehelper")
    assert not is_filehelper_identity("wechat-local:0123456789abcdef:friend")
    assert not is_filehelper_identity("wechat-local:wrong:filehelper")
    assert is_wechat_local_source("wxlocal:"+"a"*64)
    assert not is_wechat_local_source("wxlocal:short")


@pytest.mark.asyncio
async def test_disabled_channel_does_not_report_online(bridge):
    _, platform, _ = bridge
    platform.is_active = False
    assert (await bridge_status(platform, heartbeat=True)).connected is False


@pytest.mark.asyncio
async def test_scoped_preview_cannot_publish_or_authorize(bridge, monkeypatch):
    _, platform, _ = bridge
    monkeypatch.setattr(service.settings, "WECHAT_LOCAL_SCOPED_ENABLED", False)
    tag = "0123456789abcdef"
    with pytest.raises(HTTPException, match="仍在验收"):
        await publish_bridge_scope(platform, WeChatLocalScope(account_tag=tag, targets=[]))
    with pytest.raises(HTTPException, match="仍在验收"):
        await service.require_scoped_target(platform, f"wechat-local:{tag}:private:{'a' * 64}")


@pytest.mark.asyncio
async def test_v2_scope_lease_blocks_other_and_revoked_visitors(bridge, monkeypatch):
    db, platform, visitor = bridge
    tag = "0123456789abcdef"
    visitor.platform_open_id = f"wechat-local:{tag}:group:{'a' * 64}"
    db.commit()
    token = HTTPAuthorizationCredentials(scheme="Bearer", credentials=issue_visitor_im_token(visitor, timedelta(minutes=1)))
    data = {}

    class RedisFixture:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def pipeline(self, *, transaction):
            assert transaction is True
            return self

        def set(self, key, value, *, ex):
            assert ex == 45
            data[key] = value.encode()

        async def execute(self):
            pass

        async def get(self, key):
            return data.get(key)

        async def mget(self, *keys):
            return [data.get(key) for key in keys]

    monkeypatch.setattr(service.settings, "WECHAT_LOCAL_SCOPED_ENABLED", True)
    monkeypatch.setattr(service.settings, "REDIS_URL", "redis://scope-test.invalid")
    monkeypatch.setattr(service.Redis, "from_url", lambda *_args, **_kwargs: RedisFixture())
    with pytest.raises(HTTPException, match="未授权"):
        await authorize_bridge_visitor(db, platform, f"{visitor.id}-vtr", 251, token)
    scope = WeChatLocalScope(account_tag=tag, targets=[
        {"identity": visitor.platform_open_id, "keywords": ["售后"]},
    ])
    await publish_bridge_scope(platform, scope)
    assert (await bridge_status(platform)).connected
    actor, target = await authorize_bridge_visitor(db, platform, f"{visitor.id}-vtr", 251, token)
    assert actor.id == visitor.id
    assert target.allows_ai("想问售后", False)
    assert target.allows_ai("没有关键词", True)
    assert not target.allows_ai("普通群消息", False)
    with pytest.raises(ValidationError):
        WeChatLocalScope(account_tag="b" * 16, targets=scope.targets)
    await publish_bridge_scope(platform, WeChatLocalScope(account_tag=tag, targets=[]))
    with pytest.raises(HTTPException, match="未授权"):
        await authorize_bridge_visitor(db, platform, f"{visitor.id}-vtr", 251, token)


@pytest.mark.asyncio
async def test_group_unverified_mention_is_forwarded_without_ai(bridge, monkeypatch):
    from app.api.v1.endpoints import chat
    from app.schemas.chat import ChatCompletionRequest

    db, platform, visitor = bridge
    identity = f"wechat-local:0123456789abcdef:group:{'a' * 64}"
    visitor.platform_open_id = identity
    visitor.set_status_active()
    db.commit()
    target = service.ScopedWechatTarget(identity=identity, keywords=["售后"])
    monkeypatch.setattr(chat, "authorize_bridge_visitor", AsyncMock(return_value=(visitor, target)))
    monkeypatch.setattr(chat.chat_service, "validate_platform_and_project", lambda *_args: (platform, db.get(Project, platform.project_id)))
    monkeypatch.setattr(chat, "get_or_create_visitor", AsyncMock(return_value=(visitor, False)))
    monkeypatch.setattr(chat, "MessageIntentOrchestrator", lambda *_args: pytest.fail("未验证的群 @ 触发了意图识别"))
    forwarded = AsyncMock(return_value="forwarded")
    monkeypatch.setattr(chat.chat_service, "send_user_message_to_wukongim", forwarded)
    request = ChatCompletionRequest(
        api_key=platform.api_key, from_uid=identity, channel_id=f"{visitor.id}-vtr", channel_type=251,
        message="@店主 售后", stream=False, source_message_id="wxlocal:" + "b" * 64,
        forward_user_message_to_wukongim=True, extra={"wechat_local_mention_ambiguous": True},
    )
    result = await chat.chat_completion(request, db, None)
    assert result["event_type"] == "received"
    assert forwarded.await_args.kwargs["content"] == "@店主 售后"
    assert forwarded.await_args.kwargs["require_delivery"] is True
