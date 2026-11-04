from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from datetime import timedelta

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.api.v1.endpoints import chat
from app.schemas.chat import ChatCompletionRequest
from app.services import wechat_media
from app.models import Visitor
from app.schemas.chat_file_authority import VisitorFileActor
from app.services.im_access import issue_visitor_im_token, read_visitor_im_access


@pytest.mark.asyncio
async def test_revoked_upload_is_rejected_before_read_or_decode(monkeypatch):
    platform = SimpleNamespace(type="wechat_local", id=uuid4())
    visitor = Visitor(id=uuid4(), project_id=uuid4(), platform_id=platform.id)
    claims = read_visitor_im_access(issue_visitor_im_token(visitor, timedelta(minutes=1)))
    actor = VisitorFileActor(id=visitor.id, project_id=visitor.project_id, platform_id=platform.id,
        im_claims=claims, platform_key_digest="a" * 64)
    monkeypatch.setattr(chat, "authenticate_file_actor", lambda *_a: actor)
    monkeypatch.setattr(chat, "authorize_file_channel", AsyncMock())
    db = Mock()
    db.get.return_value = platform
    monkeypatch.setattr(
        chat.chat_service,
        "authenticate_staff_or_platform",
        lambda *_a: (None, platform),
    )
    authorize = AsyncMock(side_effect=HTTPException(403, "会话未授权"))
    monkeypatch.setattr(chat, "authorize_bridge_visitor", authorize)
    normalize = AsyncMock()
    monkeypatch.setattr(chat, "normalize_wechat_upload", normalize)
    file = Mock(read=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await chat.chat_file_upload(file, "test-vtr", 251, "key", None, None, db)
    assert error.value.status_code == 403
    normalize.assert_not_awaited()
    file.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_silk_is_converted_before_generic_upload_validation(monkeypatch):
    decode = Mock(return_value=b"RIFF-wav-fixture")
    monkeypatch.setattr(wechat_media, "decode_wechat_voice", decode)
    upload = UploadFile(
        BytesIO(b"silk-fixture"),
        filename="voice.silk",
        headers=Headers({"content-type": "audio/silk"}),
    )
    normalized = await wechat_media.normalize_wechat_upload(upload)
    assert normalized.content_type == "audio/wav" and normalized.filename.endswith(
        ".wav"
    )
    assert await normalized.read() == b"RIFF-wav-fixture"
    decode.assert_called_once_with(b"silk-fixture")


@pytest.mark.asyncio
async def test_unscoped_legacy_cannot_submit_media(monkeypatch):
    platform = SimpleNamespace(type="wechat_local")
    visitor = SimpleNamespace(
        platform_open_id="wechat-local:0123456789abcdef:filehelper"
    )
    monkeypatch.setattr(
        chat.chat_service,
        "validate_platform_and_project",
        lambda *_a: (platform, Mock()),
    )
    monkeypatch.setattr(
        chat, "authorize_bridge_visitor", AsyncMock(return_value=(visitor, None))
    )
    forward = AsyncMock()
    monkeypatch.setattr(chat.chat_service, "send_user_message_to_wukongim", forward)
    req = ChatCompletionRequest(
        api_key="fixture",
        from_uid=visitor.platform_open_id,
        channel_id="test-vtr",
        channel_type=251,
        message="/v1/chat/files/" + str(uuid4()),
        msg_type=2,
        source_message_id="wxlocal:" + "a" * 64,
        media_file_id=uuid4(),
    )
    with pytest.raises(HTTPException) as error:
        await chat.chat_completion(req, Mock(), None)
    assert error.value.status_code == 403
    forward.assert_not_awaited()
