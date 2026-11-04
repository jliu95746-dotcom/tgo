"""Least-privilege visitor HTTP client for the explicitly configured local TGO."""

from __future__ import annotations

import hashlib
import json
import time
from base64 import urlsafe_b64decode
from binascii import Error as Base64Error
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID, uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, ProxyHandler, Request, build_opener

from .tgo_protocol import object_map
from .media_types import MAX_MEDIA_BYTES, MediaContent, UploadedMedia


class TgoError(RuntimeError):
    pass


class TgoAuthorizationError(TgoError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"客服 API HTTP {status_code}，等待重新核对当前会话授权")


class TurnChanged(TgoError):
    pass


class TgoUnavailable(OSError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_base_url(value: str) -> str:
    base = value.rstrip("/")
    url = urlsplit(base)
    if (
        url.scheme != "http"
        or url.hostname not in ("127.0.0.1", "localhost", "::1")
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path
    ):
        raise ValueError("本轮只连接本机客服 API 根地址，拒绝远程或带凭据的地址")
    return base


def bridge_base_url(value: str, platform_type: str) -> str:
    if platform_type == "website":
        return local_base_url(value)
    base = value.rstrip("/")
    url = urlsplit(base)
    local_http = url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")
    if (
        platform_type != "wechat_local"
        or not url.hostname
        or (url.scheme != "https" and not local_http)
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or url.path not in ("", "/api")
        or any(character.isspace() for character in value)
    ):
        raise ValueError("个人微信需绑定明确的 HTTPS 或本机客服 API 地址")
    return base


def visitor_token_expired(value: object) -> bool:
    """Use the unsigned timestamp only to decide when to request a fresh grant."""
    if not isinstance(value, str):
        return True
    try:
        payload = value.split(".")[1]
        expiration = object_map(json.loads(urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))).get("exp")
        return not isinstance(expiration, (int, float)) or expiration <= time.time() + 60
    except (IndexError, ValueError, Base64Error):
        return True


def post_json(
    opener: OpenerDirector, url: str, body: Mapping[str, object], headers: Mapping[str, str]
) -> Mapping[str, object]:
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers=dict(headers),
        method="POST",
    )
    return request_json(opener, request)


def request_json(opener: OpenerDirector, request: Request) -> Mapping[str, object]:
    try:
        with opener.open(request, timeout=15) as response:
            raw = response.read(2 * 1024**2 + 1)
    except HTTPError as error:
        if error.code in (401, 403):
            raise TgoAuthorizationError(error.code) from None
        if error.code >= 500:
            raise TgoUnavailable(f"客服 API HTTP {error.code}") from None
        if error.code == 409:
            raise TurnChanged("本轮回复已变化或发送结果需要核对") from None
        raise TgoError(
            f"客服 API HTTP {error.code}，已停止该请求，不输出凭据或响应正文"
        ) from None
    except (URLError, TimeoutError, ConnectionError) as error:
        raise TgoUnavailable(f"客服连接暂不可用：{type(error).__name__}") from None
    if len(raw) > 2 * 1024**2:
        raise TgoError("客服响应超过本轮大小限制")
    result = object_map(json.loads(raw))
    if not result:
        raise TgoError("客服响应格式无效")
    return result


class TgoClient:
    def __init__(
        self, config_file: Path, runtime: Path, account_tag: str, *,
        scoped: bool = False, identity: str | None = None, nickname: str | None = None,
    ):
        self.config_file, self.runtime = config_file, runtime
        config_bytes = config_file.read_bytes()
        self.config_hash = hashlib.sha256(config_bytes).digest()
        config = object_map(json.loads(config_bytes))
        self.platform_type = str(config.get("platform_type", "website"))
        self.base = bridge_base_url(str(config.get("base_url", "")), self.platform_type)
        if (
            config.get("chat_id") != ("scoped" if scoped else "filehelper")
            or config.get("account_tag") != account_tag
            or (scoped and self.platform_type != "wechat_local")
        ):
            raise ValueError("客服配置未绑定当前微信账号及文件传输助手")
        self.api_key = str(config.get("platform_api_key", ""))
        self.platform_id = str(config.get("platform_id", ""))
        self.account_tag = account_tag
        self.scoped = scoped
        self.identity = identity or (None if scoped else f"wechat-local:{account_tag}:filehelper")
        self.nickname = nickname or "文件传输助手（微信本机测试）"
        if not self.api_key or not self.platform_id:
            raise ValueError("缺少专用测试渠道配置")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.session_file = runtime / "tgo-session.private.json"
        self.session: Mapping[str, object] = {}

    def _post(
        self, endpoint: str, body: Mapping[str, object], *, authenticated: bool = True,
        include_platform_key: bool = False,
    ) -> Mapping[str, object]:
        if hashlib.sha256(self.config_file.read_bytes()).digest() != self.config_hash:
            raise TgoError("客服绑定配置已改变，停止本轮连接")
        headers = {"Content-Type": "application/json"}
        if include_platform_key:
            headers["X-Platform-API-Key"] = self.api_key
        if authenticated:
            token = self.session.get("im_token")
            if not isinstance(token, str) or not token:
                raise TgoError("缺少当前访客凭据")
            headers["Authorization"] = "Bearer " + token
        return post_json(self.opener, self.base + endpoint, body, headers)

    def connect(self, *, force_refresh: bool = False) -> None:
        identity = self.identity
        if identity is None:
            raise TgoError("未指定授权会话")
        if self.session_file.exists():
            session = object_map(
                json.loads(self.session_file.read_text(encoding="utf-8"))
            )
            if (
                session.get("base_url") != self.base
                or session.get("platform_id") != self.platform_id
                or session.get("platform_open_id") != identity
            ):
                raise TgoError("已有客服访客不属于当前绑定，保留原凭据并停止")
            self.session = session
        if force_refresh or not self.session or (
            self.platform_type == "wechat_local" and visitor_token_expired(self.session.get("im_token"))
        ):
            previous_id = self.session.get("id")
            marker = self.runtime / "tgo-registration-inflight.private.json"
            if marker.exists():
                raise TgoError("上次访客注册结果未知，需先核对后恢复，不重复注册")
            marker.write_text(
                json.dumps(
                    {"platform_id": self.platform_id, "account_tag": self.account_tag}
                ),
                encoding="utf-8",
            )
            session = self._post(
                "/v1/visitors/register",
                {
                    "platform_api_key": self.api_key,
                    "platform_open_id": identity,
                    "name": self.nickname,
                    "nickname": self.nickname,
                    "source": "wechat-local-bridge",
                    "language": "zh-CN",
                    "custom_attributes": {} if self.scoped else {
                        "wechat_chat_id": "filehelper",
                        "integration_mode": "local-test",
                    },
                },
                authenticated=False,
            )
            required_fields = ("id", "im_token", "channel_id")
            if self.platform_type == "website":
                required_fields += ("visitor_access_token",)
            if (
                session.get("platform_id") != self.platform_id
                or session.get("platform_open_id") != identity
                or (previous_id is not None and session.get("id") != previous_id)
                or not all(
                    session.get(key)
                    for key in required_fields
                )
                or session.get("channel_type") != 251
            ):
                raise TgoError("注册响应与当前绑定不一致")
            self.session = {**session, "base_url": self.base}
            temp = self.session_file.with_suffix(".tmp")
            temp.write_text(
                json.dumps(self.session, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temp.replace(self.session_file)
            marker.unlink()
        if (
            self.session.get("channel_type") != 251
            or self.session.get("channel_id") != str(self.session.get("id")) + "-vtr"
        ):
            raise TgoError("客服访客会话绑定无效")
        self.history(0)

    @property
    def channel_id(self) -> str:
        return str(self.session["channel_id"])

    def submit(
        self, event_id: str, text: str, *, mentioned: bool = False,
        mention_ambiguous: bool = False, author: str = "", media: UploadedMedia | None = None,
        pending_kind: str | None = None,
        media_error: str | None = None,
    ) -> str:
        if media is not None and not self.scoped:
            raise TgoError("媒体仅用于已授权的接待会话")
        extra = {
            "message_id": "wxlocal:" + event_id,
            "source": "wechat-local-bridge",
        }
        if self.scoped:
            extra.update(wechat_local_mentioned=mentioned, wechat_local_author=author,
                         wechat_local_mention_ambiguous=mention_ambiguous)
        else:
            extra["chat_id"] = "filehelper"
        result = self._post(
            ("/v1/chat/turns/media-ready" if media else "/v1/chat/turns/inbound") if self.scoped else "/v1/chat/completion",
            {
                "api_key": self.api_key,
                "from_uid": self.session["platform_open_id"],
                "channel_id": self.channel_id,
                "channel_type": 251,
                "message": media.url if media else "" if pending_kind else text,
                "msg_type": (2 if (media.kind if media else pending_kind) == "image" else 4) if media or pending_kind else 1,
                "media_pending": pending_kind is not None and media_error is None,
                "media_error": media_error,
                "media_file_id": media.file_id if media else None,
                "source_message_id": "wxlocal:" + event_id,
                "extra": extra,
                "stream": False,
                "wukongim_only": True,
                "expected_output": "text",
            },
        )
        if result.get("visitor_id") and result["visitor_id"] != self.session["id"]:
            raise TgoError("客服响应访客身份不一致")
        event = result.get("event_type")
        if event == "accepted" and result.get("success") is True:
            return str(result.get("client_msg_no") or event)
        if self.scoped and event == "received" and result.get("success") is True:
            return "received"
        if event in ("human_handoff", "ai_disabled", "assist_mode", "queued"):
            return str(event)
        raise TgoError("客服接口未返回可识别的接收回执")

    def turn_replies(self) -> list[Mapping[str, object]]:
        result = self._post("/v1/chat/turns/ready", {
            "platform_api_key": self.api_key, "channel_id": self.channel_id, "channel_type": 251})
        rows = result.get("replies")
        if not isinstance(rows, list):
            raise TgoError("连续消息回复列表格式无效")
        return [object_map(row) for row in rows]

    def staff_replies(self) -> list[Mapping[str, object]]:
        if not self.scoped:
            return []
        result = self._post("/v1/chat/wechat-local-deliveries/ready", {
            "platform_api_key": self.api_key, "channel_id": self.channel_id, "channel_type": 251})
        rows = result.get("replies")
        if not isinstance(rows, list):
            raise TgoError("人工回复列表格式无效")
        return [object_map(row) for row in rows]

    def staff_action(self, delivery_id: str, action: str, **fields: object) -> Mapping[str, object]:
        if not self.scoped or action not in {"check", "claim", "receipt"}:
            raise ValueError("人工发送需要当前授权接待模式")
        identity = str(UUID(delivery_id))
        result = self._post(f"/v1/chat/wechat-local-deliveries/{identity}/{action}", {
            "platform_api_key": self.api_key, "channel_id": self.channel_id,
            "channel_type": 251, **fields})
        if action == "claim":
            try:
                UUID(str(result.get("claim_id")))
            except ValueError:
                raise TgoError("人工回复发送许可格式无效") from None
        if action == "receipt" and result.get("accepted") is not True:
            raise TgoError("云端未确认人工回复发送回执")
        return result

    def turn_action(self, turn_id: str, version: int, action: str,
                    **fields: object) -> Mapping[str, object]:
        identity = str(UUID(turn_id))
        if action not in {"check", "claim", "receipt"}:
            raise ValueError("无效的发送操作")
        return self._post(f"/v1/chat/turns/{identity}/{action}", {
            "platform_api_key": self.api_key, "channel_id": self.channel_id,
            "channel_type": 251, "version": version, **fields})

    def upload_media(self, media: MediaContent) -> UploadedMedia:
        if not self.scoped or hashlib.sha256(self.config_file.read_bytes()).digest() != self.config_hash:
            raise TgoError("媒体上传需要当前授权会话和有效绑定")
        token = self.session.get("im_token")
        if not isinstance(token, str) or not token or not 0 < len(media.content) <= MAX_MEDIA_BYTES:
            raise TgoError("媒体或访客凭据无效")
        boundary = "wechat-" + uuid4().hex
        body = bytearray()
        for name, value in (("channel_id", self.channel_id), ("channel_type", "251")):
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        # Fixed ASCII transport name keeps multipart headers portable; the UI supplies Chinese labels.
        extension = media.filename.rsplit(".", 1)[-1]
        body.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="wechat.{extension}"'
                     f'\r\nContent-Type: {media.mime_type}\r\n\r\n').encode())
        body.extend(media.content)
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        response = request_json(self.opener, Request(self.base + "/v1/chat/upload", data=bytes(body),
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                     "Authorization": "Bearer " + token, "X-Platform-API-Key": self.api_key}, method="POST"))
        if response.get("channel_id") != self.channel_id or response.get("channel_type") != 251:
            raise TgoError("媒体上传回执与授权会话不一致")
        try:
            file_id = str(UUID(str(response.get("file_id"))))
        except ValueError:
            raise TgoError("媒体上传回执缺少文件编号") from None
        return UploadedMedia(file_id, media.kind, self.base + "/v1/chat/files/" + file_id)

    def publish_scope(self, targets: list[Mapping[str, object]]) -> None:
        if not self.scoped:
            raise TgoError("当前桥接不是授权接待模式")
        response = self._post(
            f"/v1/platforms/{self.platform_id}/wechat-local/scope",
            {"account_tag": self.account_tag, "targets": targets},
            authenticated=False,
            include_platform_key=True,
        )
        if response.get("accepted") is not True:
            raise TgoError("客服系统未确认接待范围")

    def history(self, after: int) -> Mapping[str, object]:
        return self._post(
            "/v1/visitors/messages/sync",
            {
                "platform_api_key": self.api_key,
                "channel_id": self.channel_id,
                "channel_type": 251,
                "start_message_seq": after + 1 if after else 0,
                "end_message_seq": 0,
                "limit": 100,
                "pull_mode": 0,
            },
        )

    def heartbeat(self) -> None:
        if self.platform_type == "wechat_local":
            result = self._post(
                f"/v1/platforms/{self.platform_id}/wechat-local/heartbeat",
                {"channel_id": self.channel_id},
                include_platform_key=True,
            )
            if result.get("connected") is not True:
                raise TgoError("个人微信渠道未确认桥接在线")

    def public_status(self) -> Mapping[str, object]:
        return {
            "connected": bool(self.session),
            "base_url": self.base,
            "platform_id": self.platform_id,
            "visitor_id": self.session.get("id"),
            "channel_id": self.session.get("channel_id"),
        }
