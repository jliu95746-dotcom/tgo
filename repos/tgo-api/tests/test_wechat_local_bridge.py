"""Personal WeChat authorization is tied to the signed, canonical visitor."""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.models import Platform, Project, Visitor
from app.services.im_access import issue_visitor_im_token
from app.services.wechat_local_bridge import bridge_status, require_bridge_visitor
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
