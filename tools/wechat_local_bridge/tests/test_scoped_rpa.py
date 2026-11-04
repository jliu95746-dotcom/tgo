import unittest
from pathlib import Path
from threading import RLock
from unittest.mock import Mock, patch

from PIL import Image

from tools.wechat_local_bridge.reader import SessionUnread
from tools.wechat_local_bridge.rpa import RpaNotReady
from tools.wechat_local_bridge.rpa_windows import NativeRpa
from tools.wechat_local_bridge.scoped_rpa import ScopedRpa, matches_header, result_line
from tools.wechat_local_bridge.window_ocr import TextLine


class ScopedVisualGuardTests(unittest.TestCase):
    def search_backend(self):
        backend = ScopedRpa.__new__(ScopedRpa)
        backend.title = "测试联系人"
        backend.runtime = Path("unused")
        backend.authorize = Mock()
        backend.window, backend.gui, backend.navigation = Mock(), Mock(), Mock()
        backend.window.search_popup_at.return_value = False
        backend._visible_bounds = Mock(return_value=(10, 20, 800, 700))
        backend.gui.screenshot.return_value = Image.new("RGB", (570, 395), "white")
        backend.navigation.search_point.return_value = (150, 70)
        backend.navigation.focused_search.return_value = (90, 50, 1)
        backend.clipboard = Mock()
        backend.clipboard.paste.return_value = backend.title
        backend.get_clipboard = Mock(return_value="原剪贴板")
        backend.restore_clipboard = Mock()
        return backend

    def test_search_uses_one_region_for_categories_and_targets(self):
        backend = self.search_backend()
        lines = [TextLine("联系人", 30, 10), TextLine(backend.title, 120, 65),
                 TextLine("搜索网络结果", 60, 100), TextLine(backend.title, 120, 140)]
        with (
            patch("tools.wechat_local_bridge.scoped_rpa.read_lines", return_value=lines) as ocr,
            patch("tools.wechat_local_bridge.scoped_rpa.time.sleep"),
        ):
            backend._search()
        self.assertEqual(ocr.call_count, 1)
        self.assertEqual(backend.gui.click.call_args.args, (225, 171))
        self.assertNotIn(("esc",), [call.args for call in backend.gui.press.call_args_list])
        backend.restore_clipboard.assert_called_once_with("原剪贴板")

    def test_search_waits_for_local_result_without_selecting_network_result(self):
        backend = self.search_backend()
        network = [TextLine("搜索网络结果", 50, 10), TextLine(backend.title, 120, 65)]
        ready = [TextLine("联系人", 30, 10), TextLine(backend.title, 120, 65)]
        with (
            patch("tools.wechat_local_bridge.scoped_rpa.read_lines", side_effect=[network, network, ready]),
            patch("tools.wechat_local_bridge.scoped_rpa.time.sleep"),
        ):
            backend._search()
        self.assertEqual(backend.gui.click.call_count, 2)

    def test_search_confirms_result_only_while_the_verified_popup_remains(self):
        backend = self.search_backend()
        backend.window.search_popup_at.return_value = True
        lines = [TextLine("最常使用", 44, 19), TextLine(backend.title, 121, 80)]
        with (
            patch("tools.wechat_local_bridge.scoped_rpa.read_lines", return_value=lines),
            patch("tools.wechat_local_bridge.scoped_rpa.time.sleep"),
        ):
            backend._search()
        backend.gui.press.assert_any_call("enter")

    def test_search_never_confirms_with_enter_after_the_popup_has_closed(self):
        backend = self.search_backend()
        lines = [TextLine("最常使用", 44, 19), TextLine(backend.title, 121, 80)]
        with (
            patch("tools.wechat_local_bridge.scoped_rpa.read_lines", return_value=lines),
            patch("tools.wechat_local_bridge.scoped_rpa.time.sleep"),
        ):
            backend._search()
        self.assertNotIn(("enter",), [call.args for call in backend.gui.press.call_args_list])

    def test_search_stops_if_the_popup_closes_before_confirmation(self):
        backend = self.search_backend()
        backend.window.search_popup_at.side_effect = [True, False]
        lines = [TextLine("最常使用", 44, 19), TextLine(backend.title, 121, 80)]
        with (
            patch("tools.wechat_local_bridge.scoped_rpa.read_lines", return_value=lines),
            patch("tools.wechat_local_bridge.scoped_rpa.time.sleep"),
            self.assertRaisesRegex(RpaNotReady, "弹层已关闭"),
        ):
            backend._search()
        self.assertNotIn(("enter",), [call.args for call in backend.gui.press.call_args_list])

    def test_faint_category_can_be_verified_separately_without_relaxing_name_match(self):
        backend = self.search_backend()
        rows = [TextLine(backend.title, 120, 65), TextLine("群聊", 30, 139)]
        categories = [TextLine("联系人", 30, 10), TextLine("群聊", 30, 139)]
        with (
            patch("tools.wechat_local_bridge.scoped_rpa.read_lines", side_effect=[rows, categories]),
            patch("tools.wechat_local_bridge.scoped_rpa.time.sleep"),
        ):
            backend._search()
        self.assertEqual(backend.gui.click.call_args.args, (225, 171))

    def test_search_failure_rechecks_the_chat_uncovered_when_popup_closes(self):
        backend = self.search_backend()
        backend.check_target = Mock(side_effect=RpaNotReady("弹层挡住工具栏"))
        backend._sidebar = Mock(side_effect=RpaNotReady("未找到唯一会话"))
        backend._search = Mock(side_effect=RpaNotReady("分类无法核对"))
        backend._wait_target = Mock()
        backend.ensure_conversation()
        backend._wait_target.assert_called_once()

    def test_search_cleans_up_when_query_input_fails_before_readback(self):
        backend = self.search_backend()
        backend.gui.hotkey.side_effect = RpaNotReady("输入暂不可用")
        with (
            patch("tools.wechat_local_bridge.scoped_rpa.time.sleep"),
            self.assertRaisesRegex(RpaNotReady, "输入暂不可用"),
        ):
            backend._search()
        backend.gui.press.assert_called_once_with("esc")
        backend.restore_clipboard.assert_called_once_with("原剪贴板")

    def test_search_never_clicks_network_only_results(self):
        backend = self.search_backend()
        lines = [TextLine("搜索网络结果", 50, 10), TextLine(backend.title, 120, 65)]
        with (
            patch("tools.wechat_local_bridge.scoped_rpa.read_lines", return_value=lines),
            patch("tools.wechat_local_bridge.scoped_rpa.time.sleep"),
            self.assertRaises(RpaNotReady),
        ):
            backend._search()
        self.assertEqual(backend.gui.click.call_count, 1)
        backend.gui.press.assert_any_call("esc")

    def test_search_cleanup_does_not_send_keys_after_focus_is_lost(self):
        backend = self.search_backend()
        backend.window.require_point.side_effect = [None, RpaNotReady("焦点变化"), RpaNotReady("焦点变化")]
        with (
            patch("tools.wechat_local_bridge.scoped_rpa.time.sleep"),
            self.assertRaisesRegex(RpaNotReady, "焦点变化"),
        ):
            backend._search()
        backend.gui.hotkey.assert_not_called()
        backend.gui.press.assert_not_called()
        backend.restore_clipboard.assert_called_once_with("原剪贴板")

    def test_group_title_matches_only_exact_name_or_member_count(self):
        self.assertTrue(matches_header([TextLine("测 试 群 （ 12 ）", 20, 10)], "测试群", group=True))
        self.assertFalse(matches_header([TextLine("测试群二", 20, 10)], "测试群", group=True))
        self.assertFalse(matches_header([TextLine("测试群 (12)", 20, 10)], "测试群", group=False))
        self.assertFalse(matches_header([TextLine("测试群", 20, 10), TextLine("其他", 20, 25)], "测试群", group=True))

    def test_enterprise_title_recognizes_company_decoration_only(self):
        name = TextLine("同名企微", 100, 60)
        self.assertTrue(matches_header([name, TextLine("＠某企业", 130, 70)], "同名企微", group=False, enterprise=True))
        self.assertFalse(matches_header([TextLine("同名企微二", 100, 60)], "同名企微", group=False, enterprise=True))

    def test_small_header_retries_at_higher_resolution_but_still_requires_exact_name(self):
        backend = ScopedRpa.__new__(ScopedRpa)
        backend.title, backend.chat_id, backend.runtime = "测试联系人", "test", Path("unused")
        backend._header, backend._verified_header, backend.unread_state = None, None, None
        bad, good = [TextLine("测试联系入", 20, 10)], [TextLine("测试联系人", 20, 10)]
        with patch("tools.wechat_local_bridge.scoped_rpa.read_lines", side_effect=[bad, good]) as ocr:
            backend._confirm_header(Image.new("RGB", (400, 48), "white"))
        self.assertIsNotNone(backend._header)
        self.assertEqual(ocr.call_args.kwargs, {"scale": 4})
        backend._header = None
        with (
            patch("tools.wechat_local_bridge.scoped_rpa.read_lines", side_effect=[bad, bad]),
            self.assertRaisesRegex(RpaNotReady, "标题"),
        ):
            backend._confirm_header(Image.new("RGB", (400, 48), "white"))
        self.assertIsNone(backend._header)

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

    def test_frequent_category_ocr_error_does_not_relax_contact_name_matching(self):
        section = TextLine("最 常 便 用", 44, 19)
        target = TextLine("测试联系人", 121, 80)
        self.assertEqual(result_line([section, target, TextLine("群聊", 30, 139)], "测试联系人"), target)
        network = TextLine("搜索网络结果", 60, 110)
        repeated = TextLine("测试联系人", 121, 140)
        self.assertEqual(result_line([section, target, network, repeated], "测试联系人"), target)
        with self.assertRaises(RpaNotReady):
            result_line([section, target], "测试联系入")
        with self.assertRaises(RpaNotReady):
            result_line([TextLine("最常便用", 140, 19), target], "测试联系人")

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
