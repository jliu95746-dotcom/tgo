import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.wechat_local_bridge.rpa import RpaNotReady
from tools.wechat_local_bridge.rpa_windows import NativeRpa
from tools.wechat_local_bridge.window_control import WindowControl


class WindowGuardTests(unittest.TestCase):
    def test_only_same_process_search_popup_is_allowed_for_result_click(self):
        window = WindowControl.__new__(WindowControl)
        window.hwnd, window.pid = 111, 123
        window.user = Mock()
        window.user.GetAncestor.return_value = 222

        def process_id(hwnd, pointer):
            pointer._obj.value = 123

        def class_name(hwnd, value, length):
            value.value = "Qt51514QWindowToolSaveBits"
            return len(value.value)

        window.user.GetWindowThreadProcessId.side_effect = process_id
        window.user.GetClassNameW.side_effect = class_name
        with patch.object(window, "require_desktop"), patch.object(window, "bounds"):
            window.require_point(500, 800, search_result=True)
            with self.assertRaisesRegex(RpaNotReady, "遮挡"):
                window.require_point(500, 800)
            window.pid = 555
            with self.assertRaisesRegex(RpaNotReady, "遮挡"):
                window.require_point(500, 800, search_result=True)

    def test_overlay_at_editor_is_rejected_even_when_wechat_is_foreground(self):
        window = WindowControl.__new__(WindowControl)
        window.hwnd = 111
        window.user = Mock()
        window.user.WindowFromPoint.return_value = 222
        window.user.GetAncestor.return_value = 222
        with (
            patch.object(window, "require_desktop"),
            patch.object(window, "bounds"),
            self.assertRaisesRegex(RpaNotReady, "遮挡"),
        ):
            window.require_point(500, 800)

    def test_locked_desktop_prevents_window_recovery(self):
        window = WindowControl.__new__(WindowControl)
        window.user = Mock()
        window.user.OpenInputDesktop.return_value = 0
        with self.assertRaisesRegex(RpaNotReady, "桌面"):
            window.recover()
        window.user.ShowWindow.assert_not_called()

    def test_existing_draft_is_preserved_after_recovery(self):
        backend = NativeRpa.__new__(NativeRpa)
        backend.authorize = Mock()
        backend.window = Mock()
        backend.clipboard = Mock()
        backend.clipboard.paste.return_value = "原剪贴板"
        with (
            patch.object(backend, "check_target"),
            patch.object(backend, "read_input", return_value="用户草稿"),
            self.assertRaisesRegex(RpaNotReady, "已有草稿"),
        ):
            backend.ensure_ready()
        backend.clipboard.copy.assert_called_once_with("原剪贴板")
        backend.window.recover.assert_called_once()

    def test_focus_loss_before_paste_never_issues_paste_shortcut(self):
        backend = NativeRpa.__new__(NativeRpa)
        backend.scope_lock = None
        backend.gui, backend.clipboard = Mock(), Mock()
        backend.runtime = Path("unused")
        with (
            patch.object(backend, "_focus_input"),
            patch.object(backend, "_guard_input", side_effect=RpaNotReady("焦点变化")),
            self.assertRaises(RpaNotReady),
        ):
            backend.paste("测试")
        backend.gui.hotkey.assert_not_called()
