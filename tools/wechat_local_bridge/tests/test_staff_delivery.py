import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

from tools.wechat_local_bridge.reader import Message
from tools.wechat_local_bridge.rpa import RpaNotReady
from tools.wechat_local_bridge.tgo_bridge import TgoBridge
from tools.wechat_local_bridge.tgo_client import TgoError, TgoUnavailable, TurnChanged
from tools.wechat_local_bridge.tgo_store import TgoStore
from tools.wechat_local_bridge.tgo_protocol import BackendReply


DELIVERY_ID = "00000000-0000-0000-0000-000000000071"
CLAIM_ID = "00000000-0000-0000-0000-000000000072"


class StaffDeliveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runtime = Path(directory.name)
        self.store = TgoStore(self.runtime / "state.sqlite3", chat_id="original-customer")
        self.addCleanup(self.store.close)
        self.reader, self.client, self.backend = Mock(), Mock(), Mock()
        self.client.channel_id = "original-visitor-vtr"
        self.client.history.return_value = {"messages": []}
        self.client.turn_replies.return_value = []
        self.client.staff_replies.return_value = [{
            "delivery_id": DELIVERY_ID, "client_msg_no": "staff-b-message",
            "text": "这是员工 B 的回复", "created_at": "2026-11-04T15:00:00Z",
        }]
        self.client.staff_action.side_effect = self.cloud_action
        self.backend.read_input.return_value = "这是员工 B 的回复"
        self.backend.send_point.return_value = (20, 30)
        self.reader.baseline.return_value = {"message_0.db": 12}
        self.outbound = Message("message_0.db", "original-customer", 13, "server-13", 13,
                                int(time.time()), 1, "self", True, "这是员工 B 的回复")
        self.reader.history.return_value = [self.outbound]
        self.bridge = self.make_bridge(self.store)

    def make_bridge(self, store):
        return TgoBridge(self.reader, store, self.runtime, "", self.client, self.backend,
                         True, 20, 60, scoped=True)

    def cloud_action(self, identity, action, **fields):
        self.assertEqual(identity, DELIVERY_ID)
        self.assertFalse(self.bridge.desktop_lock.locked(), "Network call held desktop lock")
        if action in ("check", "claim"):
            UUID(fields["attempt_id"])
            self.assertEqual(fields["attempt_id"], self.store.next_delivery().attempt_id)
        return {"check": {"state": "queued"}, "claim": {"claim_id": CLAIM_ID},
                "receipt": {"accepted": True}}[action]

    def test_cloud_staff_reply_uses_original_conversation_once_and_reports_real_receipt(self):
        self.bridge._read_tgo()
        self.bridge._read_tgo()
        self.bridge._deliver()
        self.bridge._report_receipts()
        self.bridge._deliver()
        self.backend.click_send.assert_called_once_with((20, 30), "这是员工 B 的回复")
        receipt = [call for call in self.client.staff_action.call_args_list if call.args[1] == "receipt"]
        self.assertEqual(len(receipt), 1)
        self.assertEqual(receipt[0].kwargs, {"claim_id": CLAIM_ID, "result": "sent", "receipt": self.outbound.event_id})
        self.assertEqual(self.store.delivery_stats(), {"sent": 1})
        self.client.turn_action.assert_not_called()

    def test_claim_response_loss_reuses_persisted_attempt_after_restart(self):
        self.bridge._read_tgo()
        self.client.staff_action.side_effect = [{"state": "queued"}, TgoUnavailable("lost response")]
        with self.assertRaises(TgoUnavailable):
            self.bridge._deliver()
        attempt = self.store.next_delivery().attempt_id
        self.backend.click_send.assert_not_called()
        self.store.close()
        self.store = TgoStore(self.runtime / "state.sqlite3", chat_id="original-customer")
        self.addCleanup(self.store.close)
        self.bridge = self.make_bridge(self.store)
        self.client.staff_action.side_effect = self.cloud_action
        self.bridge._deliver()
        self.assertEqual(self.client.staff_action.call_args_list[-2].kwargs["attempt_id"], attempt)
        self.backend.click_send.assert_called_once()

    def test_transferred_owner_invalidates_queued_reply_and_preserves_human_draft(self):
        self.bridge._read_tgo()
        self.store.filled("staff-b-message")
        self.client.staff_action.side_effect = TurnChanged("owner changed")
        self.backend.clear_owned_draft.side_effect = RpaNotReady("human changed draft")
        self.bridge._deliver()
        self.backend.clear_owned_draft.assert_called_once_with("这是员工 B 的回复")
        self.backend.click_send.assert_not_called()
        self.assertEqual(self.store.delivery_stats(), {"superseded": 1})

    def test_marked_history_never_requeues_a_staff_reply(self):
        self.client.staff_replies.return_value = []
        self.client.history.return_value = {"messages": [{
            "message_seq": 9, "channel_id": "original-visitor-vtr", "channel_type": 251,
            "from_uid": "employee-b-staff", "client_msg_no": "staff-b-message",
            "payload": {"type": 1, "content": "这是员工 B 的回复",
                        "extra": {"wechat_local_delivery_id": DELIVERY_ID}},
        }]}
        self.bridge._read_tgo()
        self.assertIsNone(self.store.next_delivery())
        self.assertEqual(self.store.sequence(), 9)

    def test_ambiguous_click_is_not_repeated_and_late_receipt_can_be_reported(self):
        self.bridge._read_tgo()
        self.backend.click_send.side_effect = RuntimeError("unknown click result")
        with self.assertRaises(RuntimeError):
            self.bridge._deliver()
        self.bridge._report_receipts()
        self.assertEqual(self.store.delivery_stats(), {"uncertain": 1})
        self.bridge._reconcile_receipts()
        self.bridge._report_receipts()
        self.bridge._deliver()
        receipt_results = [call.kwargs["result"] for call in self.client.staff_action.call_args_list
                           if call.args[1] == "receipt"]
        self.assertEqual(receipt_results, ["unknown", "sent"])
        self.backend.click_send.assert_called_once()

    def test_same_reply_identifier_cannot_be_rebound_to_another_delivery_or_text(self):
        self.bridge._read_tgo()
        self.client.staff_replies.return_value[0]["text"] = "另一条回复"
        with self.assertRaises((ValueError, TgoError)):
            self.bridge._read_tgo()

    def test_staff_and_ai_turn_cannot_share_the_same_local_reply_key(self):
        self.bridge._read_tgo()
        with self.assertRaises(ValueError):
            self.store.queue_turn(key="staff-b-message", turn_id=DELIVERY_ID, version=1, text="AI 回复")

    def test_old_history_consumer_still_deduplicates_after_bridge_rollback(self):
        self.bridge._read_tgo()
        self.bridge._deliver()
        self.store.queue(BackendReply("staff-b-message", 12, "这是员工 B 的回复"))
        self.assertIsNone(self.store.next_delivery())
        self.assertEqual(self.store.sequence(), 12)
