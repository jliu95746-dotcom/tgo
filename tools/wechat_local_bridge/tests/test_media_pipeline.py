import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from tools.wechat_local_bridge.media_pipeline import MediaPipeline
from tools.wechat_local_bridge.media_types import (
    MediaContent,
    MediaNotReady,
    UploadedMedia,
)
from tools.wechat_local_bridge.reader import Message


class MediaPipelineTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.reader, self.client = Mock(), Mock()
        self.client.channel_id = "visitor-vtr"
        self.client.base = "https://test.invalid/api"
        self.reader.extract.return_value = MediaContent(
            "image", "image/jpeg", b"fixture"
        )
        self.uploaded = UploadedMedia(
            str(uuid4()), "image", "https://test.invalid/upload"
        )
        self.client.upload_media.return_value = self.uploaded
        self.message = Message(
            "message_0.db", "wxid_test", 2, "22", 2, 1, 3, "wxid_test", False, ""
        )
        self.active = Mock(return_value=True)
        self.pipeline = MediaPipeline(self.reader, self.client, self.path, self.active)

    def finish(self, pipeline):
        for _ in range(100):
            result = pipeline.prepare(self.message)
            if result is not None:
                return result
            time.sleep(0.005)
        self.fail("media task did not finish")

    def test_upload_receipt_survives_restart_without_reupload(self):
        self.assertEqual(self.finish(self.pipeline), self.uploaded)
        restarted = MediaPipeline(self.reader, self.client, self.path, self.active)
        self.assertEqual(self.finish(restarted).file_id, self.uploaded.file_id)
        self.reader.extract.assert_called_once()
        self.client.upload_media.assert_called_once()
        saved = json.loads(next(self.path.glob("*.private.json")).read_text())
        self.assertNotIn("url", saved)

    def test_not_downloaded_retries_without_upload_or_submission(self):
        self.reader.extract.side_effect = MediaNotReady("等待下载")
        self.pipeline.prepare(self.message)
        for _ in range(100):
            self.pipeline.prepare(self.message)
            if self.pipeline.retry_at:
                break
            time.sleep(0.005)
        self.assertGreater(self.pipeline.retry_at, time.monotonic())
        self.client.upload_media.assert_not_called()
        self.client.submit.assert_not_called()

    def test_revoke_between_extraction_and_upload_stops_upload(self):
        self.reader.extract.side_effect = lambda _: self.active.configure_mock(
            return_value=False
        )
        with self.assertRaises(MediaNotReady):
            self.pipeline._prepare(self.message)
        self.client.upload_media.assert_not_called()

    def test_foreign_upload_receipt_is_rejected(self):
        path = self.path / ("media-" + self.message.event_id + ".private.json")
        path.write_text(json.dumps({"channel_id": "other-vtr", "kind": "image"}))
        with self.assertRaisesRegex(ValueError, "不属于"):
            self.pipeline._prepare(self.message)
