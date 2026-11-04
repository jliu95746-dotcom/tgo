"""Prepare media outside the polling loop, retaining the upload across restarts."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
import json
from pathlib import Path
import time
from uuid import UUID

from .local_media import LocalMediaReader
from .media_types import MediaNotReady, UploadedMedia
from .reader import Message
from .tgo_client import TgoClient, TgoUnavailable

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wechat-media")


class MediaPipeline:
    def __init__(
        self,
        reader: LocalMediaReader,
        client: TgoClient,
        runtime: Path,
        active: Callable[[], bool],
    ):
        self.reader, self.client, self.runtime, self.active = (
            reader,
            client,
            runtime,
            active,
        )
        self.future: Future[UploadedMedia] | None = None
        self.event_id = ""
        self.retry_at = 0.0
        self.status = ""
        self.failures = 0
        self.failed = False

    def _prepare(self, message: Message) -> UploadedMedia:
        if not self.active():
            raise MediaNotReady("接待权限已暂停，停止处理媒体")
        path = self.runtime / ("media-" + message.event_id + ".private.json")
        kind = "image" if message.message_type == 3 else "voice"
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if (
                saved.get("channel_id") != self.client.channel_id
                or saved.get("kind") != kind
            ):
                raise ValueError("已上传媒体不属于当前会话")
            file_id = str(UUID(saved["file_id"]))
            # Regenerate an internal reference; access tokens need not persist.
            return UploadedMedia(
                file_id, kind, self.client.base + "/v1/chat/files/" + file_id
            )
        content = self.reader.extract(message)
        if not self.active():
            raise MediaNotReady("接待权限已暂停，停止上传媒体")
        uploaded = self.client.upload_media(content)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "channel_id": self.client.channel_id,
                    "kind": uploaded.kind,
                    "file_id": uploaded.file_id,
                }
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
        return uploaded

    def prepare(self, message: Message) -> UploadedMedia | None:
        if self.failed or not self.active() or time.monotonic() < self.retry_at:
            return None
        if self.future is None:
            self.event_id = message.event_id
            self.future = _executor.submit(self._prepare, message)
            self.status = "正在读取并上传微信媒体"
        if self.event_id != message.event_id:
            raise ValueError("媒体准备顺序与会话队列不一致")
        if not self.future.done():
            return None
        try:
            return self.future.result()
        except (MediaNotReady, TgoUnavailable, ValueError) as error:
            self.status = str(error)
            self.failures += 1
            self.failed = self.failures >= 5 or isinstance(error, ValueError)
            self.retry_at = time.monotonic() + min(5, 2 ** (self.failures - 1))
            self.future = None
            return None

    def completed(self) -> None:
        self.future = None
        self.event_id = ""
        self.failures = 0
        self.failed = False
        self.retry_at = 0

    def close(self) -> None:
        if self.future:
            self.future.cancel()
