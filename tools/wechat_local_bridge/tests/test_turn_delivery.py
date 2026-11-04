import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from tools.wechat_local_bridge.rpa import RpaNotReady
from tools.wechat_local_bridge.tgo_bridge import TgoBridge
from tools.wechat_local_bridge.tgo_client import TurnChanged
from tools.wechat_local_bridge.tgo_store import TgoStore


class TurnDeliveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = TgoStore(Path(folder.name) / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.reader, self.client, self.backend = Mock(), Mock(), Mock()
        self.backend.read_input.return_value = "旧回复"
        self.backend.send_point.return_value = (10, 10)
        self.reader.baseline.return_value = {}
        self.bridge = TgoBridge(
            self.reader,
            self.store,
            Path(folder.name),
            "",
            self.client,
            self.backend,
            True,
            3,
            60,
            scoped=True,
        )
        self.store.queue_turn(
            key="old",
            turn_id="00000000-0000-0000-0000-000000000001",
            version=1,
            text="旧回复",
        )

    def test_supplement_after_paste_clears_only_the_owned_draft_and_never_clicks(self):
        self.client.turn_action.side_effect = [
            {"state": "ready"},
            TurnChanged("replaced"),
        ]
        self.bridge._deliver()
        self.backend.clear_owned_draft.assert_called_once_with("旧回复")
        self.backend.click_send.assert_not_called()
        self.assertEqual(self.store.delivery_stats(), {"superseded": 1})

    def test_human_modified_draft_is_preserved_when_old_reply_is_replaced(self):
        self.store.filled("old")
        self.client.turn_action.side_effect = TurnChanged("replaced")
        self.backend.clear_owned_draft.side_effect = RpaNotReady("人工已改写")
        self.bridge._deliver()
        self.backend.click_send.assert_not_called()
        self.backend.paste.assert_not_called()
        self.assertEqual(self.store.delivery_stats(), {"superseded": 1})

    def test_window_backoff_is_short_and_does_not_claim_or_send(self):
        self.client.turn_action.return_value = {"state": "ready"}
        self.backend.ensure_ready.side_effect = RpaNotReady("窗口恢复中")
        before = time.monotonic()
        self.bridge._deliver()
        self.assertLessEqual(self.bridge.next_delivery_at - before, 1.2)
        self.assertEqual(
            [call.args[2] for call in self.client.turn_action.call_args_list], ["check"]
        )
        self.backend.click_send.assert_not_called()
