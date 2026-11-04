"""Check WeChat scope and file ownership before media enters chat."""

import asyncio
from io import BytesIO
from urllib.parse import urlsplit
from typing import Literal

from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Platform, Visitor
from app.schemas.chat import ChatCompletionRequest
from app.schemas.chat_media import ChatMediaInput
from app.services.chat_media_service import ChatMediaService
from app.services.wechat_voice import (
    MAX_SILK_BYTES,
    WechatVoiceError,
    decode_wechat_voice,
)
from app.services.wechat_image import (
    MAX_IMAGE_BYTES,
    WechatImageError,
    decode_wechat_image,
)


def authorize_wechat_media(
    db: Session,
    platform: Platform,
    visitor: Visitor,
    request: ChatCompletionRequest,
) -> None:
    reference, expected = urlsplit(request.message), urlsplit(
        settings.API_BASE_URL
    )
    if reference.netloc and (reference.scheme, reference.netloc) != (
        expected.scheme,
        expected.netloc,
    ):
        raise HTTPException(403, "个人微信媒体须使用当前客服系统上传的文件")
    if request.media_file_id is None:
        raise HTTPException(422, "微信媒体缺少已上传文件编号")
    if request.msg_type not in (2, 4):
        raise HTTPException(422, "不支持的微信媒体消息类型")
    message_type: Literal[2, 4] = 2 if request.msg_type == 2 else 4
    ChatMediaService(db).authorize(
        ChatMediaInput(
            project_id=platform.project_id,
            platform_id=platform.id,
            visitor_id=visitor.id,
            source_message_id=request.source_message_id or "",
            message_type=message_type,
            reference=request.message,
            file_id=request.media_file_id,
        )
    )


async def normalize_wechat_upload(file: UploadFile) -> UploadFile:
    mime = (file.content_type or "").lower()
    if mime == "application/vnd.wechat.wxgf":
        content = await file.read(MAX_IMAGE_BYTES + 1)
        try:
            image = await asyncio.to_thread(decode_wechat_image, content)
        except WechatImageError as error:
            raise HTTPException(422, str(error)) from error
        return UploadFile(
            BytesIO(image),
            filename="微信图片.jpg",
            size=len(image),
            headers=Headers({"content-type": "image/jpeg"}),
        )
    if mime == "audio/silk":
        content = await file.read(MAX_SILK_BYTES + 1)
        try:
            wav = await asyncio.to_thread(decode_wechat_voice, content)
        except WechatVoiceError as error:
            raise HTTPException(422, str(error)) from error
        return UploadFile(
            BytesIO(wav),
            filename="微信语音.wav",
            size=len(wav),
            headers=Headers({"content-type": "audio/wav"}),
        )
    if mime not in {"image/jpeg", "image/png", "audio/wav"}:
        raise HTTPException(422, "个人微信目前支持图片和语音消息")
    return file
