import unittest
from threading import RLock
from unittest.mock import Mock, patch

from PIL import Image

from tools.wechat_local_bridge.reader import SessionUnread
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

    def test_enterprise_title_recognizes_company_decoration_only(self):
        name = TextLine("同名企微", 100, 60)
        self.assertTrue(matches_header([name, TextLine("＠某企业", 130, 70)], "同名企微", group=False, enterprise=True))
        self.assertFalse(matches_header([TextLine("同名企微二", 100, 60)], "同名企微", group=False, enterprise=True))

    def test_search_rejects_duplicate_and_uncategorized_text(self):
        row = TextLine("测 试 群", 100, 60)
        section = TextLine("群聊", 30, 25)
        self.assertEqual(result_line([section, row], "测试群"), row)
        second = TextLine("测试群", 105, 100)
        with self.assertRaises(RpaNotReady):
            result_line([section, row, second], "测试群")
        self.assertEqual(result_line([section, row, second], "测试群", candidate=1), second)
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
        backend._verified_marker = None
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
        backend.unread_state = None
        backend.scope_lock = RLock()
        backend._header, backend._verified_header = b"target", None
        backend.get_clipboard = Mock(return_value="original")
        backend.restore_clipboard = Mock()
        backend._contacts_tab = Mock()
        backend._chat_tab = Mock()
        backend.ensure_conversation = Mock()
        backend._clear_marker = Mock()
        backend.draft_matches = Mock(side_effect=lambda _marker: backend._chat_tab.called)
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
        backend.unread_state = None
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
            patch("tools.wechat_local_bridge.scoped_rpa.time.monotonic", side_effect=[0, 6]),
        ):
            with self.assertRaises(RuntimeError):
                backend.ensure_ready()
            with self.assertRaises(RpaNotReady):
                backend.paste("客户回复")
        paste.assert_called_once_with("桥接自检-marker")
        backend._clear_marker.assert_not_called()

    def test_duplicate_chat_replaces_only_the_verified_marker(self):
        backend = ScopedRpa.__new__(ScopedRpa)
        backend._header = backend._verified_header = b"target"
        backend._verified_marker = "local marker"
        backend.scope_lock = RLock()
        backend.bounds = (0, 0, 800, 600)
        backend.read_input = Mock(side_effect=["another draft", "local marker", "客服回复"])
        backend.clipboard, backend.gui, backend._guard_input = Mock(), Mock(), Mock()
        backend._context_digest = Mock(return_value=b"verified history")
        with patch("tools.wechat_local_bridge.scoped_rpa.time.sleep"):
            with self.assertRaises(RpaNotReady):
                backend.paste("客服回复")
            backend.gui.hotkey.assert_not_called()
            backend.paste("客服回复")
        self.assertEqual([call.args for call in backend.gui.hotkey.call_args_list], [("ctrl", "a"), ("ctrl", "v")])
        self.assertEqual(backend._verified_context, b"verified history")
        self.assertIsNone(backend._verified_marker)

    def test_chat_context_excludes_editor_but_detects_changed_messages(self):
        backend = ScopedRpa.__new__(ScopedRpa)
        backend._header_box = (20, 10, 100, 24)
        backend.target = Mock(toolbar=(20, 95, 60, 12), input_box=(20, 70, 100, 92))
        frame = Image.new("RGB", (120, 100), "white")
        verified = backend._context_digest(frame)
        frame.paste("black", (25, 74, 60, 80))
        self.assertEqual(backend._context_digest(frame), verified)
        frame.paste("black", (25, 40, 60, 45))
        self.assertNotEqual(backend._context_digest(frame), verified)

    def test_duplicate_enterprise_chat_cannot_paste_without_read_transition(self):
        backend = ScopedRpa.__new__(ScopedRpa)
        backend.unread_state = Mock(side_effect=[SessionUnread(2, 10, 20), SessionUnread(0, 11, 21)])
        backend.window, backend._sidebar, backend._wait_target = Mock(), Mock(), Mock()
        backend.get_clipboard = Mock(return_value="original")
        backend.restore_clipboard = Mock()
        backend.draft_matches = Mock()
        with (
            patch.object(NativeRpa, "ensure_ready"),
            patch.object(NativeRpa, "paste") as paste,
            patch("tools.wechat_local_bridge.scoped_rpa.time.monotonic", side_effect=[0, 1, 3]),
            patch("tools.wechat_local_bridge.scoped_rpa.time.sleep"),
        ):
            with self.assertRaises(RpaNotReady):
                backend.ensure_ready()
            paste.assert_not_called()
        backend.draft_matches.assert_not_called()
        self.assertIsNone(backend._verified_header)

    def test_duplicate_enterprise_chat_accepts_matching_read_transition(self):
        backend = ScopedRpa.__new__(ScopedRpa)
        backend.unread_state = Mock(return_value=SessionUnread(0, 11, 20))
        backend._confirm_unread_transition(SessionUnread(2, 10, 20))

    def test_duplicate_navigation_tries_second_row_only_with_unchanged_session(self):
        backend = ScopedRpa.__new__(ScopedRpa)
        before = SessionUnread(2, 10, 20)
        backend.unread_state = Mock(return_value=before)
        backend.window, backend._sidebar, backend._wait_target = Mock(), Mock(), Mock()
        backend._confirm_unread_transition = Mock(side_effect=[RpaNotReady(), None])
        backend._open_unread_same_title(before)
        self.assertEqual([call.args for call in backend._sidebar.call_args_list], [(0,), (1,)])
