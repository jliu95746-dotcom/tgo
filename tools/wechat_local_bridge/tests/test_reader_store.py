import sqlite3
import tempfile
import unittest
from pathlib import Path

import zstandard

from tools.wechat_local_bridge.reader import Message, conversation_table, read_messages
from tools.wechat_local_bridge.store import InboxStore


class ReaderTests(unittest.TestCase):
    def test_only_target_conversation_is_read_and_zstd_is_decoded(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE Name2Id(user_name TEXT)")
        connection.execute("INSERT INTO Name2Id VALUES ('test_self')")
        table = conversation_table("filehelper")
        connection.execute(
            f'CREATE TABLE "{table}"(local_id INTEGER, server_id INTEGER, local_type INTEGER, sort_seq INTEGER, real_sender_id INTEGER, create_time INTEGER, message_content BLOB, compress_content BLOB)'
        )
        content = zstandard.ZstdCompressor().compress("接入测试消息".encode())
        connection.execute(
            f'INSERT INTO "{table}" VALUES (7,99,1,8,1,1700000000,?,NULL)', (content,)
        )
        result = read_messages(
            connection, "message_0.db", "filehelper", "test_self", 0, 20
        )
        self.assertEqual(result[0].text, "接入测试消息")
        self.assertTrue(result[0].is_self)
        self.assertEqual(
            read_messages(connection, "message_0.db", "filehelper", "test_self", 7, 20),
            [],
        )
        with self.assertRaises(ValueError):
            conversation_table("")


class StoreTests(unittest.TestCase):
    def test_baseline_dedup_and_restart_preserve_pending_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            store = InboxStore(path)
            store.baseline({"message_0.db": 5})
            event = Message(
                "message_0.db",
                "filehelper",
                6,
                "9",
                6,
                1700000000,
                1,
                "test_self",
                True,
                "接入测试-1",
            )
            self.assertTrue(store.ingest(event, "收到接入测试-1"))
            self.assertFalse(store.ingest(event, "重复"))
            self.assertEqual(store.cursors()["message_0.db"], 6)
            store.close()
            resumed = InboxStore(path)
            draft = resumed.pending_draft()
            self.assertEqual(draft["reply"], "收到接入测试-1")
            resumed.acknowledge(draft["event_id"])
            self.assertIsNone(resumed.pending_draft())
            resumed.close()

    def test_unapproved_conversation_never_becomes_a_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            store = InboxStore(Path(directory) / "state.sqlite3")
            event = Message(
                "message_0.db",
                "another_chat",
                1,
                "1",
                1,
                1700000000,
                1,
                "someone",
                False,
                "接入测试-1",
            )
            with self.assertRaisesRegex(ValueError, "文件传输助手"):
                store.ingest(event, "不可填写")
            store.close()


if __name__ == "__main__":
    unittest.main()
