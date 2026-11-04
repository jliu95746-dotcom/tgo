from dataclasses import replace
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.wechat_local_bridge.local_media import LocalMediaReader
from tools.wechat_local_bridge.media_types import MediaNotReady
from tools.wechat_local_bridge.reader import Message


class LocalMediaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript(
            """
            CREATE TABLE Name2Id(user_name);
            INSERT INTO Name2Id VALUES ('wxid_allowed'), ('wxid_other');
            CREATE TABLE VoiceInfo(chat_name_id,local_id,svr_id,create_time,voice_data);
            INSERT INTO VoiceInfo VALUES (2,7,'77',100,X'02232153494c4b5f56336f74686572');
        """
        )
        self.entry = SimpleNamespace(
            path=root / "db_storage/message/media_0.db", key=b"fixture"
        )
        self.reader = SimpleNamespace(
            chat_id="wxid_allowed",
            root=root / "db_storage",
            databases=[self.entry],
            shards=[],
            self_username="wxid_self",
        )
        self.active = Mock(return_value=True)
        self.media = LocalMediaReader(
            self.reader, root / "keys.private.json", root, self.active
        )
        self.message = Message(
            "message/message_0.db",
            "wxid_allowed",
            7,
            "77",
            7,
            100,
            34,
            "wxid_allowed",
            False,
            "",
        )

    def test_same_local_ids_in_other_chat_never_match_voice(self):
        with patch(
            "tools.wechat_local_bridge.local_media.open_snapshot",
            return_value=MockConnection(self.db),
        ):
            with self.assertRaisesRegex(MediaNotReady, "未唯一匹配"):
                self.media._voice(self.message)
            self.db.execute(
                "INSERT INTO VoiceInfo VALUES (1,7,'77',100,?)",
                (b"\x02#!SILK_V3allowed",),
            )
            result = self.media._voice(self.message)
            self.assertEqual(result.content, b"\x02#!SILK_V3allowed")
            with self.assertRaises(MediaNotReady):
                self.media._voice(replace(self.message, server_id="other"))

    def test_revocation_and_foreign_message_stop_before_any_database_read(self):
        with patch("tools.wechat_local_bridge.local_media.open_snapshot") as snapshot:
            with self.assertRaises(MediaNotReady):
                self.media.extract(replace(self.message, chat_id="wxid_other"))
            self.active.return_value = False
            with self.assertRaises(MediaNotReady):
                self.media.extract(self.message)
            snapshot.assert_not_called()

    def test_conflicting_voice_payloads_are_not_guessed(self):
        for content in (b"#!SILK_V3one", b"#!SILK_V3two"):
            self.db.execute("INSERT INTO VoiceInfo VALUES (1,7,'77',100,?)", (content,))
        with patch(
            "tools.wechat_local_bridge.local_media.open_snapshot",
            return_value=MockConnection(self.db),
        ):
            with self.assertRaises(MediaNotReady):
                self.media._voice(self.message)


class MockConnection:
    def __init__(self, db):
        self.db = db

    def execute(self, *args):
        return self.db.execute(*args)

    def close(self):
        pass
