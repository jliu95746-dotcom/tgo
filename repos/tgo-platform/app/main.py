from __future__ import annotations
import asyncio
import base64
import uuid
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from fastapi import FastAPI, Request
from app.api.error_utils import register_exception_handlers

from app.core.config import settings
from app.api.v1 import health, messages
from app.api.v1 import platforms as platforms_v1
from app.api.v1 import callbacks as callbacks_v1
from app.api.v1 import internal as internal_v1
from app.infra.http import HttpxTgoApiClient
from app.infra.sse import DefaultSSEManager
from app.db.base import SessionLocal
from app.domain.services.normalizer import normalizer
from app.domain.services.listeners import EmailChannelListener
from app.domain.services.listeners.media_cleanup_listener import MediaCleanupListener
from app.domain.services.listeners.wecom_listener import WeComChannelListener
from app.domain.services.listeners.wecom_media_listener import WeComMediaListener
from app.domain.services.media.storage import (
    EncryptedLocalMediaStorage,
    LocalMediaObjectDeleter,
)
from app.domain.services.media.observability import InMemoryMediaMetrics
from app.domain.services.media.wecom_downloader import WeComMediaDownloader
from app.domain.services.listeners.wukongim_listener import WuKongIMChannelListener
from app.domain.services.listeners.feishu_listener import FeishuChannelListener
from app.domain.services.listeners.dingtalk_listener import DingTalkChannelListener
from app.domain.services.listeners.telegram_listener import TelegramChannelListener
from app.domain.services.listeners.slack_listener import SlackChannelListener


# Configure logging after imports so static import checks remain deterministic.
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
)
# HTTPX logs full query strings at INFO, which can contain WeCom credentials.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create shared clients
    app.state.tgo_api_client = HttpxTgoApiClient(settings.api_base_url, timeout=settings.request_timeout_seconds)
    app.state.sse_manager = DefaultSSEManager()

    # Start multi-tenant Email listener supervisor (dynamic; safe if no email platforms exist)
    app.state.email_listener = EmailChannelListener(
        session_factory=SessionLocal,
        normalizer=normalizer,
        tgo_api_client=app.state.tgo_api_client,
        sse_manager=app.state.sse_manager,
    )
    # Start WeCom consumer (processes pending wecom_inbox messages)
    app.state.wecom_listener = WeComChannelListener(
        session_factory=SessionLocal,
        normalizer=normalizer,
        tgo_api_client=app.state.tgo_api_client,
        sse_manager=app.state.sse_manager,
    )
    app.state.wecom_media_listener = None
    app.state.media_cleanup_listener = None
    app.state.media_metrics = InMemoryMediaMetrics(
        concurrency_limit=settings.media_job_max_concurrency
    )
    media_storage_path = Path(settings.media_storage_path)
    if settings.media_cleanup_enabled:
        app.state.media_cleanup_listener = MediaCleanupListener(
            session_factory=SessionLocal,
            object_deleter=LocalMediaObjectDeleter(root=media_storage_path),
            metrics=app.state.media_metrics,
        )
    if settings.media_ingestion_enabled:
        if settings.media_encryption_key is None:
            raise RuntimeError(
                "MEDIA_ENCRYPTION_KEY is required when media ingestion is enabled"
            )
        try:
            encryption_key = base64.b64decode(
                settings.media_encryption_key.get_secret_value(),
                validate=True,
            )
        except ValueError as exc:
            raise RuntimeError(
                "MEDIA_ENCRYPTION_KEY must be valid base64"
            ) from exc
        media_storage = EncryptedLocalMediaStorage(
            root=media_storage_path,
            encryption_key=encryption_key,
            key_id=settings.media_encryption_key_id,
        )
        media_downloader = WeComMediaDownloader(
            timeout_seconds=settings.media_download_timeout_seconds,
        )
        app.state.wecom_media_listener = WeComMediaListener(
            session_factory=SessionLocal,
            downloader=media_downloader,
            storage=media_storage,
            metrics=app.state.media_metrics,
            max_concurrency=settings.media_job_max_concurrency,
        )
    # Start WuKongIM consumer (processes pending wukongim_inbox messages)
    app.state.wukongim_listener = WuKongIMChannelListener(
        session_factory=SessionLocal,
        normalizer=normalizer,
        tgo_api_client=app.state.tgo_api_client,
        sse_manager=app.state.sse_manager,
    )

    # Start Feishu Bot consumer (processes pending feishu_inbox messages)
    app.state.feishu_listener = FeishuChannelListener(
        session_factory=SessionLocal,
        normalizer=normalizer,
        tgo_api_client=app.state.tgo_api_client,
        sse_manager=app.state.sse_manager,
    )

    # Start DingTalk Bot consumer (processes pending dingtalk_inbox messages)
    app.state.dingtalk_listener = DingTalkChannelListener(
        session_factory=SessionLocal,
        normalizer=normalizer,
        tgo_api_client=app.state.tgo_api_client,
        sse_manager=app.state.sse_manager,
    )

    # Start Telegram Bot consumer (uses getUpdates polling)
    app.state.telegram_listener = TelegramChannelListener(
        session_factory=SessionLocal,
        normalizer=normalizer,
        tgo_api_client=app.state.tgo_api_client,
        sse_manager=app.state.sse_manager,
    )

    # Start Slack Bot consumer (uses Socket Mode WebSocket)
    app.state.slack_listener = SlackChannelListener(
        session_factory=SessionLocal,
        normalizer=normalizer,
        tgo_api_client=app.state.tgo_api_client,
        sse_manager=app.state.sse_manager,
    )

    if app.state.media_cleanup_listener is not None:
        await app.state.media_cleanup_listener.start()
    if app.state.wecom_media_listener is not None:
        await app.state.wecom_media_listener.start()
    app.state.email_listener_task = asyncio.create_task(app.state.email_listener.start())
    app.state.wukongim_listener_task = asyncio.create_task(app.state.wukongim_listener.start())
    app.state.feishu_listener_task = asyncio.create_task(app.state.feishu_listener.start())
    app.state.dingtalk_listener_task = asyncio.create_task(app.state.dingtalk_listener.start())
    app.state.telegram_listener_task = asyncio.create_task(app.state.telegram_listener.start())
    app.state.wecom_listener_task = asyncio.create_task(app.state.wecom_listener.start())
    app.state.slack_listener_task = asyncio.create_task(app.state.slack_listener.start())


    try:
        yield
    finally:
        # Shutdown: stop listeners and close http client
        await app.state.email_listener.stop()
        await app.state.wecom_listener.stop()
        if app.state.wecom_media_listener is not None:
            await app.state.wecom_media_listener.stop()
        if app.state.media_cleanup_listener is not None:
            await app.state.media_cleanup_listener.stop()
        await app.state.wukongim_listener.stop()
        await app.state.feishu_listener.stop()
        await app.state.dingtalk_listener.stop()
        await app.state.telegram_listener.stop()
        await app.state.slack_listener.stop()
        app.state.email_listener_task.cancel()
        app.state.wecom_listener_task.cancel()
        app.state.wukongim_listener_task.cancel()
        app.state.feishu_listener_task.cancel()
        app.state.dingtalk_listener_task.cancel()
        app.state.telegram_listener_task.cancel()
        app.state.slack_listener_task.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.email_listener_task
        with suppress(asyncio.CancelledError):
            await app.state.wecom_listener_task
        with suppress(asyncio.CancelledError):
            await app.state.wukongim_listener_task
        with suppress(asyncio.CancelledError):
            await app.state.feishu_listener_task
        with suppress(asyncio.CancelledError):
            await app.state.dingtalk_listener_task
        with suppress(asyncio.CancelledError):
            await app.state.telegram_listener_task
        with suppress(asyncio.CancelledError):
            await app.state.slack_listener_task
        await app.state.tgo_api_client.aclose()

app = FastAPI(lifespan=lifespan, docs_url="/v1/docs", redoc_url="/v1/redoc")

# Register global exception handlers and request ID middleware
register_exception_handlers(app)

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["x-request-id"] = rid
    return response





app.include_router(health.router, tags=["health"])
app.include_router(messages.router, tags=["messages"])
app.include_router(platforms_v1.router, tags=["platforms"])
app.include_router(callbacks_v1.router, tags=["callbacks"])

app.include_router(internal_v1.router, tags=["internal"])
