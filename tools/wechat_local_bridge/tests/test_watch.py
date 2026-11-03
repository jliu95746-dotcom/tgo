import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from tools.wechat_local_bridge.reader import Message
from tools.wechat_local_bridge.store import InboxStore
from tools.wechat_local_bridge.watch import Watcher


class WatchTests(unittest.TestCase):
    def test_failed_rpa_outcome_is_never_automatically_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            reader = Mock()
            event = Message(
                "message/message_0.db",
                "filehelper",
                1,
                "1",
                1,
                1700000000,
                1,
                "test_self",
                True,
                "任意测试内容",
            )
            reader.new_messages.side_effect = [[event], []]
            store = InboxStore(runtime / "state.sqlite3")
            backend = Mock()
            backend.read_input.return_value = "错误内容"
            watcher = Watcher(reader, store, runtime, "", backend)
            watcher.poll()
            watcher.poll()
            backend.paste.assert_called_once()
            self.assertEqual(store.stats(), {"uncertain": 1})
            store.close()

    def test_empty_key_capture_does_not_overwrite_existing_keys(self):
        from tools.wechat_local_bridge.memory import save_keys

        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            target = runtime / "keys.private.json"
            target.write_text("previous-valid-file")
            with self.assertRaises(ValueError):
                save_keys(runtime, 1, [])
            self.assertEqual(target.read_text(), "previous-valid-file")

    def test_private_runtime_cannot_change_unrelated_directory_permissions(self):
        from tools.wechat_local_bridge.private_runtime import ensure_private_runtime

        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            ensure_private_runtime(Path(directory))


if __name__ == "__main__":
    unittest.main()
