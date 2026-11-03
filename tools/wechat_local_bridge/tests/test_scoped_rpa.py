import unittest
from threading import RLock
from unittest.mock import Mock, patch

from tools.wechat_local_bridge.rpa import RpaNotReady
from tools.wechat_local_bridge.rpa_windows import NativeRpa
from tools.wechat_local_bridge.scoped_rpa import ScopedRpa, matches_header, result_line
from tools.wechat_local_bridge.window_ocr import TextLine


class ScopedVisualGuardTests(unittest.TestCase):
    def test_group_title_matches_only_exact_name_or_member_count(self):
        self.assertTrue(matches_header([TextLine("测 试 群 （ 12 ）", 20, 10)], "测试群", group=True))
        self.assertFalse(matches_header([TextLine("测试群二", 20, 10)], "测试群", group=True))
        self.assertFalse(matches_header([TextLine("测试群 (12)", 20, 10)], "测试群", group=False))
        self.assertFalse(matches_header([TextLine("测试群", 20, 10), TextLine("其他", 20, 25)], "测试群", group=True))

    def test_search_rejects_duplicate_and_uncategorized_text(self):
        row = TextLine("测 试 群", 100, 60)
        section = TextLine("群聊", 30, 25)
        self.assertEqual(result_line([section, row], "测试群"), row)
        with self.assertRaises(RpaNotReady):
            result_line([section, row, TextLine("测试群", 105, 100)], "测试群")
        with self.assertRaises(RpaNotReady):
            result_line([TextLine("聊天记录", 30, 25), row], "测试群")
        network = TextLine("搜索网络结果", 30, 80)
        repeated = TextLine("测试群", 110, 100)
        self.assertEqual(result_line([row, repeated], "测试群", [network]), row)
        with self.assertRaises(RpaNotReady):
            result_line([repeated], "测试群", [network])

    def test_customer_reply_requires_proof_for_current_header(self):
        backend = ScopedRpa.__new__(ScopedRpa)
        backend._header, backend._verified_header = b"current", None
        with patch.object(NativeRpa, "paste") as paste:
            with self.assertRaises(RpaNotReady):
                backend.paste("客户回复")
            backend._verified_header = b"another"
            with self.assertRaises(RpaNotReady):
                backend.paste("客户回复")
            paste.assert_not_called()
            backend._verified_header = b"current"
            backend.paste("客户回复")
            paste.assert_called_once_with("客户回复")

    def test_marker_is_cleared_only_after_scoped_database_verifies_it(self):
        backend = ScopedRpa.__new__(ScopedRpa)
        backend.scope_lock = RLock()
        backend._header, backend._verified_header = b"target", None
        backend.get_clipboard = Mock(return_value="original")
        backend.restore_clipboard = Mock()
        backend._contacts_tab = Mock()
        backend._chat_tab = Mock()
        backend.ensure_conversation = Mock()
        backend._clear_marker = Mock()
        backend.draft_matches = Mock(side_effect=[False, True])
        backend.read_input = Mock(return_value="桥接自检-marker")
        with (
            patch.object(NativeRpa, "ensure_ready"),
            patch.object(NativeRpa, "paste") as paste,
            patch("tools.wechat_local_bridge.scoped_rpa.uuid.uuid4", return_value=Mock(hex="marker")),
        ):
            backend.ensure_ready()
        paste.assert_called_once_with("桥接自检-marker")
        backend._contacts_tab.assert_called_once()
        backend._clear_marker.assert_called_once_with("桥接自检-marker")
        backend.restore_clipboard.assert_called_once_with("original")
        self.assertEqual(backend._verified_header, b"target")

    def test_missing_scoped_draft_proof_never_unlocks_reply_paste(self):
        backend = ScopedRpa.__new__(ScopedRpa)
        backend._header, backend._verified_header = b"target", None
        backend.get_clipboard = Mock(return_value="original")
        backend.restore_clipboard = Mock()
        backend._contacts_tab = Mock()
        backend._chat_tab = Mock()
        backend.ensure_conversation = Mock()
        backend._clear_marker = Mock()
        backend.draft_matches = Mock(return_value=False)
        backend.read_input = Mock(return_value="桥接自检-marker")
        with (
            patch.object(NativeRpa, "ensure_ready"),
            patch.object(NativeRpa, "paste") as paste,
            patch("tools.wechat_local_bridge.scoped_rpa.uuid.uuid4", return_value=Mock(hex="marker")),
            patch("tools.wechat_local_bridge.scoped_rpa.time.monotonic", side_effect=[0, 4]),
        ):
            with self.assertRaises(RuntimeError):
                backend.ensure_ready()
            with self.assertRaises(RpaNotReady):
                backend.paste("客户回复")
        paste.assert_called_once_with("桥接自检-marker")
        backend._clear_marker.assert_not_called()
