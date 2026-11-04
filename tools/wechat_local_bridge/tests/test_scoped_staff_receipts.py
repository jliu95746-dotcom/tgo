"""The production scheduler must reconcile frozen sends without clicking again."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from tools.wechat_local_bridge.reader import Message
from tools.wechat_local_bridge.reception_scope import Contact, Grant
from tools.wechat_local_bridge.scoped_bridge import ScopedBridge, Worker, cloud_identity
from tools.wechat_local_bridge.tgo_bridge import TgoBridge
from tools.wechat_local_bridge.tgo_client import TgoError, TgoUnavailable
from tools.wechat_local_bridge.tgo_store import TgoStore


class ScopedStaffReceiptTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runtime = Path(directory.name)
        self.store = TgoStore(self.runtime / "state.sqlite3", chat_id="customer")
        self.addCleanup(self.store.close)
        self.store.baseline({"message_0.db": 12})
        self.store.bind("original-visitor-binding", 0)
        self.reader, self.client, self.backend = Mock(), Mock(), Mock()
        self.client.account_tag = "b" * 16
        self.client.channel_id = "original-visitor-vtr"
        self.client.session = {}
        self.client.staff_action.side_effect = self.cloud_action
        self.backend.read_input.return_value = "人工回复"
        self.backend.send_point.return_value = (20, 30)
        self.backend.click_send.side_effect = RuntimeError("click outcome unknown")
        self.reader.baseline.return_value = {"message_0.db": 12}
        self.reader.history.return_value = []
        self.bridge = TgoBridge(self.reader, self.store, self.runtime, "", self.client,
                                self.backend, True, 20, 60, scoped=True)
        self.bridge.poll = Mock()
        self.grant = Grant(Contact("customer", "测试好友", "", False, True), "a" * 32, ())
        self.service = ScopedBridge(Mock(), self.client, self.runtime, True)
        self.addCleanup(self.service.network_pool.shutdown)
        self.addCleanup(self.service.delivery_pool.shutdown)
        self.service._active = Mock(return_value=True)
        self.service._load_backend = Mock()
        self.service.published = [{"identity": cloud_identity(self.client.account_tag, self.grant),
                                  "keywords": []}]
        self.worker = Worker(self.grant, self.bridge, connected=True)
        self.store.queue_staff(key="staff-message", delivery_id="00000000-0000-0000-0000-000000000071",
                               text="人工回复")
        self.receipt = Message("message_0.db", "customer", 13, "server-13", 13,
                               int(time.time()), 1, "self", True, "人工回复")

    def cloud_action(self, identity, action, **fields):
        self.assertFalse(self.service.desktop_lock.locked())
        return {"check": {"state": "queued"},
                "claim": {"claim_id": "00000000-0000-0000-0000-000000000072"},
                "receipt": {"accepted": True}}[action]

    def freeze(self):
        self.service._deliver_worker(self.worker)
        self.assertTrue(self.worker.error)
        self.assertEqual(self.store.delivery_stats(), {"uncertain": 1})

    def test_scheduler_keeps_unknown_frozen_then_recovers_unique_late_receipt(self):
        self.freeze()
        error = self.worker.error
        self.service._process(self.worker)
        self.assertEqual(self.worker.error, error)
        self.assertEqual(self.store.delivery_stats(), {"uncertain": 1})
        self.reader.history.return_value = [self.receipt]
        self.service._process(self.worker)
        self.assertEqual(self.store.delivery_stats(), {"sent": 1})
        self.assertEqual(self.worker.error, "")
        self.assertEqual(self.store.pending_staff_receipts(), [])
        results = [call.kwargs["result"] for call in self.client.staff_action.call_args_list
                   if call.args[1] == "receipt"]
        self.assertEqual(results, ["unknown", "sent"])
        self.backend.click_send.assert_called_once()
        self.bridge.poll.assert_not_called()

    def test_recovered_send_stays_frozen_until_cloud_acknowledges(self):
        self.freeze()
        error = self.worker.error
        self.reader.history.return_value = [self.receipt]
        self.client.staff_action.side_effect = TgoUnavailable("receipt network unavailable")
        self.service._process(self.worker)
        self.assertEqual(self.worker.error, error)
        self.assertEqual(self.store.delivery_stats(), {"sent": 1})
        self.assertEqual(len(self.store.pending_staff_receipts()), 1)
        self.client.staff_action.side_effect = self.cloud_action
        self.service._process(self.worker)
        self.assertEqual(self.worker.error, "")
        self.assertEqual(self.store.pending_staff_receipts(), [])
        self.backend.click_send.assert_called_once()

    def test_unrelated_worker_error_is_not_cleared_by_a_late_receipt(self):
        self.freeze()
        self.worker.error = "消息序号回退，暂停该会话"
        self.reader.history.return_value = [self.receipt]
        self.reader.history.reset_mock()
        self.service._process(self.worker)
        self.assertEqual(self.worker.error, "消息序号回退，暂停该会话")
        self.reader.history.assert_not_called()
        self.backend.click_send.assert_called_once()

    def test_new_unrelated_error_during_receipt_lookup_is_preserved(self):
        self.freeze()

        def late_receipt(_limit):
            self.worker.error = "消息序号回退，暂停该会话"
            return [self.receipt]

        self.reader.history.side_effect = late_receipt
        self.service._process(self.worker)
        self.assertEqual(self.store.delivery_stats(), {"sent": 1})
        self.assertEqual(self.worker.error, "消息序号回退，暂停该会话")
        self.backend.click_send.assert_called_once()

    def test_restart_uncertain_send_also_enters_read_only_recovery(self):
        self.freeze()
        self.worker = Worker(self.grant, self.bridge)
        self.bridge.initialize = Mock(side_effect=TgoError("发现上次未确认的发送"))
        self.service._process(self.worker)
        self.assertTrue(self.worker.error)
        self.reader.history.return_value = [self.receipt]
        self.service._process(self.worker)
        self.assertEqual(self.worker.error, "")
        self.backend.click_send.assert_called_once()
        self.bridge.initialize.assert_called_once()
        self.bridge.poll.assert_not_called()
