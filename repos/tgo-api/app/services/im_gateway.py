"""Authorized JSON-RPC relay for SaaS clients and the private IM service."""

import asyncio
import json
from contextlib import suppress
from time import monotonic, time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from pydantic import JsonValue, TypeAdapter, ValidationError
from starlette.websockets import WebSocketState
from websockets.exceptions import ConnectionClosed
from websockets.legacy.client import WebSocketClientProtocol, connect

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import TGOAPIException
from app.core.logging import get_logger
from app.services.im_access import authenticate_im_actor, authorize_im_channel
from app.services.im_credentials import transport_token
from app.services.wukongim_client import wukongim_client

logger = get_logger("im_gateway")
RPC = dict[str, JsonValue]
OBJECT = TypeAdapter(RPC)
MAX_FRAME_BYTES = 262144
MAX_PENDING = 1024
REVALIDATE_SECONDS = 10
SEND_FIELDS = {
    "channelId",
    "channelType",
    "clientMsgNo",
    "payload",
    "header",
    "setting",
    "topic",
    "expire",
    "msgKey",
}


def gateway_urls() -> tuple[str, str]:
    """Only configured URLs may select a transport; never client input."""
    urls = (settings.SAAS_IM_PUBLIC_URL, settings.SAAS_IM_UPSTREAM_URL)
    for value in urls:
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in ("ws", "wss")
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                raise ValueError("Invalid gateway URL")
            # Accessing port also validates its integer value and range.
            _ = parsed.port
        except ValueError as exc:
            raise HTTPException(503, "实时消息入口尚未配置") from exc
    return urls


def parse_frame(raw: str | bytes) -> RPC:
    if (
        len(raw.encode("utf-8") if isinstance(raw, str) else raw)
        > MAX_FRAME_BYTES
    ):
        raise ValueError("Frame too large")
    return OBJECT.validate_json(raw)


def object_field(frame: RPC, name: str) -> RPC:
    value = frame.get(name, {})
    if not isinstance(value, dict):
        raise ValueError("Expected object")
    return value


def string_field(frame: RPC, name: str, maximum: int = 128) -> str:
    value = frame.get(name)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("Invalid string field")
    return value


def channel_fields(params: RPC) -> tuple[str, int]:
    channel_id = string_field(params, "channelId")
    channel_type = params.get("channelType")
    if type(channel_type) is not int or not 0 < channel_type < 256:
        raise ValueError("Invalid channel type")
    return channel_id, channel_type


def ack_fields(params: RPC) -> tuple[str, int]:
    message_id = string_field(params, "messageId")
    sequence = params.get("messageSeq")
    if type(sequence) is not int or sequence < 0:
        raise ValueError("Invalid message sequence")
    return message_id, sequence


class MessageRelay:
    def __init__(self, browser: WebSocket, uid: str, token: str) -> None:
        self.browser = browser
        self.uid = uid
        self.token = token
        self.pending: set[str] = set()
        self.delivered: set[tuple[str, int]] = set()

    async def verify(self, channel: tuple[str, int] | None = None, *, sending: bool = False) -> None:
        # A short session prevents a socket from retaining stale ORM records.
        with SessionLocal() as db:
            actor = authenticate_im_actor(db, self.uid, self.token)
            if channel is not None:
                await authorize_im_channel(db, actor, *channel)
                if sending:
                    from app.services.company_entitlements import require_human_service, require_new_service
                    if channel[1] == 251:
                        require_human_service(db, actor.project_id, UUID(channel[0].removesuffix("-vtr")))
                    else:
                        require_new_service(db, actor.project_id)

    async def error(self, request_id: str, code: int) -> None:
        await self.browser.send_json(
            {
                "id": request_id,
                "error": {"code": code, "message": "实时消息请求未获授权"},
            }
        )

    async def from_browser(self, upstream: WebSocketClientProtocol) -> None:
        while True:
            frame = parse_frame(await self.browser.receive_text())
            await self.verify()
            request_id = frame.get("id")
            if request_id is not None:
                request_id = string_field(frame, "id")
            method = frame.get("method")
            params = object_field(frame, "params")
            if method == "send":
                if not isinstance(request_id, str):
                    raise ValueError("Send request requires an id")
                try:
                    if not set(params).issubset(SEND_FIELDS):
                        raise ValueError("Unknown send fields")
                    string_field(params, "payload", MAX_FRAME_BYTES)
                    string_field(params, "clientMsgNo")
                    await self.verify(channel_fields(params), sending=True)
                except (TGOAPIException, HTTPException) as exc:
                    if exc.status_code not in (400, 402, 403, 404):
                        raise
                    await self.error(request_id, 403)
                    continue
                except ValueError:
                    await self.error(request_id, 400)
                    continue
            elif method == "recvack":
                key = ack_fields(params)
                if key not in self.delivered:
                    continue
                self.delivered.remove(key)
            elif method != "ping":
                if isinstance(request_id, str):
                    await self.error(request_id, 403)
                    continue
                raise ValueError("Unsupported method")
            if isinstance(request_id, str):
                if (
                    request_id in self.pending
                    or len(self.pending) >= MAX_PENDING
                ):
                    raise ValueError("Too many or duplicate requests")
                self.pending.add(request_id)
            await upstream.send(json.dumps(frame))

    async def from_upstream(self, upstream: WebSocketClientProtocol) -> None:
        while True:
            raw = await upstream.recv()
            frame = parse_frame(raw)
            await self.verify()
            request_id = frame.get("id")
            if isinstance(request_id, str):
                if request_id not in self.pending:
                    continue
                self.pending.remove(request_id)
            elif frame.get("method") in ("recv", "event"):
                params = object_field(frame, "params")
                try:
                    await self.verify(channel_fields(params))
                except (TGOAPIException, HTTPException) as exc:
                    if exc.status_code not in (400, 403, 404):
                        raise
                    if frame.get("method") == "recv":
                        await self.ack_discarded(upstream, params)
                    continue
                except ValueError:
                    # Events without verifiable channel context stay private.
                    continue
                if frame.get("method") == "recv":
                    if len(self.delivered) >= MAX_PENDING:
                        raise ValueError(
                            "Client is not acknowledging messages"
                        )
                    self.delivered.add(ack_fields(params))
            elif frame.get("method") not in ("pong", "disconnect"):
                continue
            await self.browser.send_text(
                raw if isinstance(raw, str) else raw.decode("utf-8")
            )

    async def ack_discarded(
        self,
        upstream: WebSocketClientProtocol,
        params: RPC,
    ) -> None:
        message_id, sequence = ack_fields(params)
        await upstream.send(
            json.dumps(
                {
                    "method": "recvack",
                    "params": {
                        "messageId": message_id,
                        "messageSeq": sequence,
                        "header": params.get("header", {}),
                    },
                }
            )
        )

    async def check_liveness(self) -> None:
        while True:
            await asyncio.sleep(REVALIDATE_SECONDS)
            await self.verify()

    async def run(self, upstream: WebSocketClientProtocol) -> None:
        tasks = [
            asyncio.create_task(operation)
            for operation in (
                self.from_browser(upstream),
                self.from_upstream(upstream),
                self.check_liveness(),
            )
        ]
        try:
            done, _ = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def serve_im_gateway(browser: WebSocket) -> None:
    if not settings.SAAS_ENABLED:
        await browser.close(code=1008)
        return
    try:
        _, upstream_url = gateway_urls()
    except HTTPException:
        await browser.close(code=1013)
        return
    await browser.accept()
    request_id: str | None = None
    established = False
    close_code = 1000
    stage = "connect_frame"
    started_at = monotonic()
    failure_type: str | None = None
    try:
        initial = parse_frame(
            await asyncio.wait_for(browser.receive_text(), 5)
        )
        if initial.get("method") != "connect":
            raise ValueError("Connect must be first")
        request_id = string_field(initial, "id")
        params = object_field(initial, "params")
        uid = string_field(params, "uid")
        token = string_field(params, "token", 8192)
        relay = MessageRelay(browser, uid, token)
        stage = "identity"
        await relay.verify()
        stage = "upstream_registration"
        await wukongim_client.register_or_login_user(uid=uid, token=token)
        stage = "upstream_connect"
        async with connect(
            upstream_url,
            open_timeout=5,
            close_timeout=2,
            max_size=MAX_FRAME_BYTES,
            max_queue=16,
        ) as upstream:
            stage = "upstream_auth"
            # Never forward a client-selected privileged device identifier.
            await upstream.send(
                json.dumps(
                    {
                        "id": request_id,
                        "method": "connect",
                        "params": {
                            "uid": uid,
                            "token": transport_token(uid, token),
                            "deviceId": str(uuid4()),
                            "deviceFlag": settings.WUKONGIM_DEVICE_FLAG,
                            "clientTimestamp": int(time() * 1000),
                        },
                    }
                )
            )
            response = parse_frame(await asyncio.wait_for(upstream.recv(), 5))
            if response.get("id") != request_id or "error" in response:
                raise ValueError("Upstream authentication failed")
            if not isinstance(response.get("result"), dict):
                raise ValueError("Invalid upstream authentication result")
            await relay.verify()
            stage = "browser_response"
            await browser.send_json(response)
            established = True
            await relay.run(upstream)
    except TGOAPIException as exc:
        failure_type = type(exc).__name__
        close_code = 1008
        if request_id and not established:
            await browser.send_json(
                {
                    "id": request_id,
                    "error": {"code": 401, "message": "实时消息凭据无效或已过期"},
                }
            )
    except (ValueError, KeyError, ValidationError, TimeoutError) as exc:
        failure_type = type(exc).__name__
        close_code = 1008
    except (WebSocketDisconnect, ConnectionClosed) as exc:
        failure_type = type(exc).__name__
        close_code = 1000
    except Exception as exc:
        failure_type = type(exc).__name__
        close_code = 1011
        logger.warning(
            "Realtime relay failed", extra={"error_type": type(exc).__name__}
        )
    finally:
        if not established and failure_type:
            # Operational diagnosis must never include credentials or frames.
            logger.warning(
                "Realtime handshake failed",
                extra={
                    "stage": stage,
                    "error_type": failure_type,
                    "elapsed_ms": int((monotonic() - started_at) * 1000),
                },
            )
        if browser.application_state == WebSocketState.CONNECTED:
            with suppress(RuntimeError):
                await browser.close(code=close_code)
