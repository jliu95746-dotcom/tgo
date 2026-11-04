import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from tools.wechat_local_bridge.reader import Message
from tools.wechat_local_bridge.rpa import RpaNotReady
from tools.wechat_local_bridge.tgo_bridge import TgoBridge
from tools.wechat_local_bridge.tgo_client import TgoError, TgoUnavailable
from tools.wechat_local_bridge.tgo_store import TgoStore


def wechat_message(local_id, text):
    return Message(
        "message_0.db",
        "filehelper",
        local_id,
        str(local_id),
        local_id,
        int(time.time()),
        1,
        "self",
        True,
        text,
    )


class TgoBridgeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        runtime = Path(folder.name)
        self.store = TgoStore(runtime / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.reader, self.client, self.rpa = Mock(), Mock(), Mock()
        self.reader.baseline.return_value = {"message_0.db": 21}
        self.client.base = "http://127.0.0.1:1234"
        self.client.channel_id = "visitor-vtr"
        self.client.platform_id = "test-platform"
        self.client.history.return_value = {"messages": []}
        self.client.submit.return_value = "ai-test"
        self.client.turn_replies.return_value = []
        self.rpa.read_input.return_value = "客服回复"
        self.rpa.send_point.return_value = (900, 800)
        self.bridge = TgoBridge(
            self.reader, self.store, runtime, "", self.client, self.rpa, True, 3, 60
        )
        self.bridge.initialize()
        self.client.history.return_value = {
            "messages": [
                {
                    "message_seq": 1,
                    "channel_id": "visitor-vtr",
                    "channel_type": 251,
                    "from_uid": "staff-staff",
                    "client_msg_no": "ai-test",
                    "payload": {"type": 1, "content": "客服回复"},
                }
            ]
        }
        self.inbound = wechat_message(22, "手机测试问题")
        self.outbound = wechat_message(23, "客服回复")
        self.reader.new_messages.side_effect = [[self.inbound], [self.outbound]]
        self.reader.history.return_value = [self.inbound, self.outbound]

    def test_same_account_phone_message_is_forwarded_and_own_reply_is_not(self):
        self.bridge.poll()
        self.bridge.poll()
        self.client.submit.assert_called_once_with(
            self.inbound.event_id, self.inbound.text
        )
        self.rpa.click_send.assert_called_once_with((900, 800), "客服回复")
        self.assertEqual(self.store.delivery_stats(), {"sent": 1})
        self.assertEqual(self.store.stats(), {"accepted": 1, "echo": 1})

    def test_own_reply_is_still_recognized_after_wechat_assigns_server_id(self):
        provisional = replace(self.outbound, server_id="0")
        synced = replace(self.outbound, server_id="123456")
        self.reader.history.return_value = [self.inbound, provisional]
        self.reader.new_messages.side_effect = [[self.inbound], [synced]]

        self.bridge.poll()
        self.bridge.poll()

        self.client.submit.assert_called_once()
        self.assertEqual(self.store.stats(), {"accepted": 1, "echo": 1})
        self.assertFalse(self.store.is_echo(replace(synced, local_id=24)))

    def test_user_draft_blocks_send_without_losing_tgo_reply(self):
        self.rpa.ensure_ready.side_effect = RpaNotReady("已有草稿")
        self.bridge.poll()
        self.rpa.click_send.assert_not_called()
        self.assertEqual(self.store.delivery_stats(), {"pending": 1})

    def test_submit_timeout_recovers_from_history_without_posting_twice(self):
        self.client.submit.side_effect = TgoUnavailable("TimeoutError")
        self.client.history.return_value = {"messages": []}
        self.reader.new_messages.side_effect = [[self.inbound], []]
        self.bridge.poll()
        self.assertEqual(self.store.stats(), {"submit_uncertain": 1})
        self.client.history.return_value = {
            "messages": [
                {
                    "message_seq": 1,
                    "channel_id": "visitor-vtr",
                    "channel_type": 251,
                    "from_uid": "visitor-vtr",
                    "client_msg_no": "visitor-copy",
                    "payload": {
                        "type": 1,
                        "content": self.inbound.text,
                        "extra": {"message_id": "wxlocal:" + self.inbound.event_id},
                    },
                }
            ]
        }
        self.bridge.poll()
        self.client.submit.assert_called_once()
        self.assertEqual(self.store.stats(), {"accepted": 1})
        self.rpa.click_send.assert_not_called()

    def test_restart_sends_persisted_reply_without_a_new_inbound_or_backend_reply(self):
        self.rpa.ensure_ready.side_effect = RpaNotReady("搜索结果尚未就绪")
        self.bridge.poll()
        self.assertEqual(self.store.delivery_stats(), {"pending": 1})
        self.rpa.ensure_ready.side_effect = None
        self.reader.new_messages.side_effect = None
        self.reader.new_messages.return_value = []
        self.client.history.return_value = {"messages": []}
        runtime = self.bridge.runtime
        path = runtime / "state.sqlite3"
        self.store.close()
        reopened = TgoStore(path)
        self.addCleanup(reopened.close)
        recovered = TgoBridge(self.reader, reopened, runtime, "", self.client, self.rpa, True, 3, 60)
        recovered.initialize()
        recovered.poll()
        self.assertEqual(reopened.delivery_stats(), {"sent": 1})
        self.rpa.click_send.assert_called_once_with((900, 800), "客服回复")
        self.client.submit.assert_called_once()

    def test_uncertain_click_is_never_retried_after_restart(self):
        self.rpa.click_send.side_effect = RuntimeError("unknown click outcome")
        self.reader.history.return_value = []
        with self.assertRaises(RuntimeError):
            self.bridge.poll()
        with self.assertRaisesRegex(TgoError, "未确认"):
            self.bridge.initialize()
        self.assertEqual(self.store.delivery_stats(), {"uncertain": 1})
        self.rpa.click_send.assert_called_once()

    def test_restart_uses_late_unique_wechat_receipt_without_clicking_again(self):
        self.rpa.click_send.side_effect = RuntimeError("transport interrupted after click")
        with self.assertRaises(RuntimeError):
            self.bridge.poll()
        self.bridge.initialize()
        self.assertEqual(self.store.delivery_stats(), {"sent": 1})
        self.rpa.click_send.assert_called_once()

    def test_scoped_media_placeholder_precedes_upload_and_self_media_is_ignored(self):
        from tools.wechat_local_bridge.media_types import UploadedMedia
        self.bridge.scoped = True
        self.bridge.media = Mock()
        self.bridge.media.prepare.side_effect = [None, UploadedMedia("file-id", "image", "/v1/chat/files/file-id")]
        self.client.account_tag = "fixture"
        incoming = replace(self.inbound, message_type=3, text="", sender_id="filehelper", is_self=False)
        self.reader.new_messages.side_effect = None
        self.reader.new_messages.return_value = []
        self.client.history.return_value = {"messages": []}
        self.bridge.observe([incoming, replace(incoming, local_id=25, is_self=True)])
        self.bridge.poll(read_local=False)
        self.assertEqual(self.client.submit.call_args.kwargs["pending_kind"], "image")
        self.assertEqual(self.store.stats(), {"media_pending": 1, "ignored": 1})
        self.bridge.poll(read_local=False)
        self.assertEqual(self.client.submit.call_args.kwargs["media"].file_id, "file-id")
        self.assertEqual(self.store.stats(), {"accepted": 1, "ignored": 1})
        self.bridge.media.completed.assert_called()
