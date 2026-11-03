import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.wechat_local_bridge.reader import Message, structured_mentions
from tools.wechat_local_bridge.reception_scope import Contact, ContactGroup, Scope, ScopeSettings, load_contacts
from tools.wechat_local_bridge.scoped_bridge import ScopedBridge, cloud_identity
from tools.wechat_local_bridge.rpa import RpaNotReady
from tools.wechat_local_bridge.tgo_bridge import TgoBridge
from tools.wechat_local_bridge.tgo_protocol import BackendReply
from tools.wechat_local_bridge.tgo_store import TgoStore


class ScopeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        reader = Mock(root=self.root, databases=[SimpleNamespace(path=self.root / "contact.db")])
        self.scope = Scope(reader, self.root / "scope.private.json")
        self.people = {
            "a": Contact("a", "甲", "同名", False, True),
            "b": Contact("b", "", "同名", False, True),
            "c": Contact("c", "", "其他人", False, True),
            "1@chatroom": Contact("1@chatroom", "", "售后群", True, True),
        }
        loader = patch("tools.wechat_local_bridge.reception_scope.load_contacts",
                       side_effect=lambda _: self.people.copy())
        self.loader = loader.start()
        self.addCleanup(loader.stop)

    def test_group_pause_rename_removal_and_explicit_restore(self):
        settings = ScopeSettings(True, ("a",), ("c",), (ContactGroup("售后", ("b", "c"), True),),
                                 {"1@chatroom": ("售后",)})
        self.scope.save(settings, [])
        self.assertEqual(set(self.scope.grants()), {"a", "b", "1@chatroom"})
        self.assertFalse(self.scope.unique_title("b"))
        old = self.scope.grants()["a"]
        self.people["a"] = replace(self.people["a"], remark="改名")
        self.scope.refresh(force=True)
        self.assertEqual(self.scope.grants()["a"].revision, old.revision)
        del self.people["a"]
        self.scope.refresh(force=True)
        self.assertNotIn("a", self.scope.grants())
        self.people["a"] = old.contact
        self.scope.refresh(force=True)
        self.assertNotIn("a", self.scope.grants())
        self.scope.save(settings, ["a"])
        self.assertNotEqual(cloud_identity("a" * 16, old), cloud_identity("a" * 16, self.scope.grants()["a"]))
        self.scope.save(replace(settings, groups=()), [])
        self.assertNotIn("b", self.scope.grants())

    def test_revocation_and_unknown_schema_fail_closed(self):
        settings = ScopeSettings(True, ("a",))
        self.scope.save(settings, [])
        bridge = ScopedBridge(self.scope, Mock(), self.root, allow_send=True)
        grant = self.scope.grants()["a"]
        bridge.authorize(grant, check_title=True)
        self.loader.side_effect = sqlite3.OperationalError("unknown schema")
        self.scope.refresh(force=True)
        self.assertEqual(self.scope.settings, settings)
        self.assertEqual(self.scope.suspended, set())
        with self.assertRaises(RpaNotReady):
            bridge.authorize(grant)
        self.scope.save(replace(settings, enabled=False), [])
        self.assertFalse(self.scope.settings.enabled)

    def test_contact_schema_excludes_nonfriends_and_groups_no_longer_joined(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript("""
            CREATE TABLE contact(username, remark, nick_name, local_type, flag, delete_flag, verify_flag, is_in_chat_room);
            CREATE TABLE chat_room(username);
            INSERT INTO contact VALUES ('wxid_a','备注','昵称',1,1,0,0,0),
                ('wxid_stranger','','访客',1,0,0,0,0), ('wxid_removed','','已删除',1,1,1,0,0),
                ('r1@chatroom','','售后群',2,0,0,0,1), ('r2@chatroom','','退出的群',2,0,0,0,0);
            INSERT INTO chat_room VALUES ('r1@chatroom'), ('r2@chatroom');
        """)
        reader = SimpleNamespace(databases=[SimpleNamespace(path=self.root / "contact.db", key=b"fixture")],
                                 self_username="wxid_self")
        with patch("tools.wechat_local_bridge.reception_scope.open_snapshot", return_value=connection):
            contacts = load_contacts(reader)
        self.assertEqual({key for key, value in contacts.items() if value.selectable}, {"wxid_a", "r1@chatroom"})
        self.assertEqual(contacts["wxid_a"].title, "备注")


class ScopedMessageTests(unittest.TestCase):
    def test_mentions_use_explicit_sender_ids_and_reject_unknown_xml(self):
        self.assertEqual(structured_mentions("<msgsource><atuserlist><![CDATA[wxid_self,wxid_other]]></atuserlist></msgsource>"),
                         (("wxid_self", "wxid_other"), True))
        self.assertEqual(structured_mentions("<msgsource><atuserlist>@所有人</atuserlist></msgsource>"), ((), False))
        self.assertEqual(structured_mentions("<!DOCTYPE a><msgsource/>"), ((), False))
        self.assertEqual(structured_mentions("<msgsource/>"), ((), True))

    def test_group_isolated_no_prefix_and_revocation_blocks_pending_send(self):
        with tempfile.TemporaryDirectory() as directory, closing(
            TgoStore(Path(directory) / "state.sqlite3", chat_id="room@chatroom")
        ) as ledger:
            path = Path(directory)
            reader, client, backend = Mock(), Mock(), Mock()
            reader.self_username = "wxid_self"
            reader.baseline.return_value = {"message_0.db": 21}
            client.account_tag = "a" * 16
            client.history.return_value = {"messages": []}
            client.submit.return_value = "received"
            active = Mock(return_value=True)
            bridge = TgoBridge(reader, ledger, path, "", client, backend, True, 3, 60, scoped=True, active=active)
            message = Message("message_0.db", "room@chatroom", 22, "22", 22, 1, 1,
                              "wxid_sender", False, "@店主 售后", mentions_verified=True)
            bridge.observe([message])
            bridge.poll(read_local=False)
            self.assertTrue(client.submit.call_args.kwargs["mention_ambiguous"])
            self.assertFalse(client.submit.call_args.kwargs["mentioned"])
            self.assertEqual(client.submit.call_args.args[1], "@店主 售后")
            bridge.observe([replace(message, local_id=23, mentioned_ids=("wxid_self",))])
            bridge.poll(read_local=False)
            self.assertTrue(client.submit.call_args.kwargs["mentioned"])
            self.assertFalse(client.submit.call_args.kwargs["mention_ambiguous"])
            with self.assertRaises(ValueError):
                bridge.observe([replace(message, chat_id="other@chatroom")])
            ledger.queue(BackendReply("reply-1", 1, "客服回复"))
            active.return_value = False
            bridge.poll(read_local=False)
            backend.click_send.assert_not_called()
            self.assertEqual(ledger.delivery_stats(), {"pending": 1})
            self.assertEqual(client.submit.call_count, 2)
            active.return_value = True
            backend.read_input.return_value = "客服回复"
            backend.send_point.return_value = (10, 20)
            receipt = replace(message, local_id=24, sender_id="wxid_self", is_self=True, text="客服回复")
            reader.history.return_value = [replace(receipt, chat_id="other@chatroom"), receipt]
            bridge.poll(read_local=False)
            backend.click_send.assert_called_once()
            self.assertEqual(ledger.delivery_stats(), {"sent": 1})
