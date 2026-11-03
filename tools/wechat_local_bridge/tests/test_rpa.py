import unittest
from unittest.mock import Mock

from tools.wechat_local_bridge.rpa import RpaNotReady, fill_draft


class RpaTests(unittest.TestCase):
    def test_nonempty_or_unapproved_target_is_never_touched(self):
        backend = Mock()
        backend.ensure_ready.side_effect = RpaNotReady("已有草稿")
        with self.assertRaises(RpaNotReady):
            fill_draft("测试回复", backend)
        backend.paste.assert_not_called()

    def test_clipboard_is_restored_and_verification_is_mandatory(self):
        backend = Mock()
        backend.get_clipboard.return_value = "原剪贴板"
        backend.read_input.return_value = "测试回复"
        result = fill_draft("测试回复", backend)
        self.assertTrue(result["input_readback_matches"])
        backend.restore_clipboard.assert_called_once_with("原剪贴板")
        self.assertFalse(result["sent"])

    def test_unknown_input_outcome_is_not_reported_as_success(self):
        backend = Mock()
        backend.read_input.return_value = "未填写正确"
        with self.assertRaisesRegex(RuntimeError, "回读"):
            fill_draft("测试回复", backend)
        backend.restore_clipboard.assert_called_once()

    def test_focus_loss_after_paste_is_uncertain_not_retryable(self):
        backend = Mock()
        backend.read_input.side_effect = RpaNotReady("前台丢失")
        with self.assertRaises(RuntimeError) as result:
            fill_draft("测试回复", backend)
        self.assertNotIsInstance(result.exception, RpaNotReady)
        backend.restore_clipboard.assert_called_once()


if __name__ == "__main__":
    unittest.main()
