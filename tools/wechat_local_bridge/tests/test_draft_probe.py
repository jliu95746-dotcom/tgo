import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from tools.wechat_local_bridge.draft_probe import DraftProbe
from tools.wechat_local_bridge.rpa import RpaNotReady


class DraftProbeTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.probe = DraftProbe(self.root, "target")
        self.marker = "桥接自检-" + "a" * 32

    def test_restart_recovers_only_the_recorded_marker_in_its_own_chat(self):
        self.probe.remember(self.marker)
        restarted = DraftProbe(self.root, "target")
        clear = Mock()
        self.assertTrue(restarted.recover(lambda: self.marker, lambda value: value == self.marker, clear))
        clear.assert_called_once_with(self.marker)
        self.assertIsNone(restarted.pending())

    def test_duplicate_conversation_retains_marker_until_verified_reply_replaces_it(self):
        self.probe.remember(self.marker)
        clear = Mock()
        self.assertTrue(self.probe.recover(lambda: self.marker, lambda _: True, clear, keep_marker=True))
        clear.assert_not_called()
        self.assertEqual(self.probe.pending(), self.marker)

    def test_user_edits_are_preserved(self):
        self.probe.remember(self.marker)
        clear = Mock()
        with self.assertRaises(RpaNotReady):
            self.probe.recover(lambda: "用户自己的草稿", lambda _: True, clear)
        clear.assert_not_called()
        self.assertEqual(self.probe.pending(), self.marker)

    def test_marker_without_its_target_database_proof_is_preserved(self):
        self.probe.remember(self.marker)
        clear = Mock()
        with self.assertRaises(RpaNotReady):
            self.probe.recover(lambda: self.marker, lambda _: False, clear)
        clear.assert_not_called()
        self.assertEqual(self.probe.pending(), self.marker)

    def test_empty_editor_cannot_discard_a_saved_marker_until_database_catches_up(self):
        self.probe.remember(self.marker)
        with self.assertRaises(RpaNotReady):
            self.probe.recover(lambda: "", lambda _: True, Mock())
        self.probe.recover(lambda: "", lambda _: False, Mock())
        self.assertIsNone(self.probe.pending())

    def test_a_different_channel_target_cannot_adopt_the_journal(self):
        self.probe.remember(self.marker)
        with self.assertRaises(RpaNotReady):
            DraftProbe(self.root, "another-target").pending()

    def test_corrupt_journal_and_unowned_markers_are_rejected(self):
        with self.assertRaises(ValueError):
            self.probe.remember("用户草稿")
        self.probe.path.write_text(json.dumps({"chat_id": "target", "marker": "unowned"}), encoding="utf-8")
        with self.assertRaises(RpaNotReady):
            self.probe.pending()

    def test_clear_failure_retains_recovery_information(self):
        self.probe.remember(self.marker)
        with self.assertRaises(RpaNotReady):
            self.probe.recover(lambda: self.marker, lambda _: True, Mock(side_effect=RpaNotReady("焦点变化")))
        self.assertEqual(self.probe.pending(), self.marker)
