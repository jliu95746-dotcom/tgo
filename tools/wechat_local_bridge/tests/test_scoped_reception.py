import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from tools.wechat_local_bridge import private_runtime
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

    def test_same_name_enterprise_contacts_require_proof_and_both_authorizations(self):
        remark_id, nickname_id = "1001@openim", "1002@openim"
        self.people[remark_id] = Contact(remark_id, "同名企微", "另一个昵称", False, True)
        self.people[nickname_id] = Contact(nickname_id, "", "同名企微", False, True)
        settings = ScopeSettings.parse({"enabled": True, "individuals": [remark_id, nickname_id]})
        self.scope.save(settings, [])
        bridge = ScopedBridge(self.scope, Mock(), self.root, allow_send=True)
        for identity in (remark_id, nickname_id):
            grant = self.scope.grants()[identity]
            bridge.authorize(grant)
            self.assertIn(":private:", cloud_identity("a" * 16, grant))
            with self.assertRaises(RpaNotReady):
                bridge.authorize(grant, check_title=True)
            bridge.authorize(grant, check_title=True, verify_same_title=True)
        self.scope.save(replace(settings, individuals=(nickname_id,)), [])
        with self.assertRaises(RpaNotReady):
            bridge.authorize(self.scope.grants()[nickname_id], check_title=True, verify_same_title=True)

    def test_worker_reuses_ledger_and_session_after_shortening_legacy_path(self):
        self.scope.save(ScopeSettings(True, ("a",)), [])
        grant = self.scope.grants()["a"]
        old = self.root / "targets" / sha256(b"a").hexdigest() / grant.revision
        with closing(TgoStore(old / "state.sqlite3", chat_id="a")) as ledger:
            ledger.baseline({"message_0.db": 7})
        (old / "tgo-session.private.json").write_text("fixture session", encoding="utf-8")
        bridge = ScopedBridge(self.scope, Mock(), self.root, allow_send=False)
        current = bridge.target_runtime(grant)
        self.assertLess(len(str(current)), len(str(old)))
        self.assertFalse(old.exists())
        self.assertEqual((current / "tgo-session.private.json").read_text(encoding="utf-8"), "fixture session")
        with closing(TgoStore(current / "state.sqlite3", chat_id="a")) as ledger:
            self.assertEqual(ledger.cursors(), {"message_0.db": 7})
        self.assertEqual(bridge.target_runtime(grant), current)
        self.assertNotEqual(bridge.target_runtime(replace(grant, revision=uuid4().hex)), current)

    @unittest.skipUnless(os.name == "nt", "Windows local application data")
    def test_fresh_channel_uses_short_application_data_and_reuses_existing_binding(self):
        channel = uuid4()
        extracted = self.root / ("extracted-" + "a" * 90) / ".tmp/wechat-local-bridge"
        legacy = extracted / "scoped-channels" / str(channel)
        profile = self.root / "Local"
        with (
            patch.dict(os.environ, {"LOCALAPPDATA": str(profile)}),
            patch.object(private_runtime, "DEFAULT_RUNTIME", extracted),
        ):
            self.assertEqual(private_runtime.scoped_runtime(channel),
                             profile / "Yujian" / "WeChatBridge" / str(channel))
            legacy.mkdir(parents=True)
            (legacy / "tgo.private.json").touch()
            self.assertEqual(private_runtime.scoped_runtime(channel), legacy)

    def test_contact_schema_excludes_nonfriends_and_groups_no_longer_joined(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript("""
            CREATE TABLE contact(username, remark, nick_name, local_type, flag, delete_flag, verify_flag, is_in_chat_room);
            CREATE TABLE chat_room(username);
            INSERT INTO contact VALUES ('wxid_a','备注','昵称',1,1,0,0,0),
                ('wxid_stranger','','访客',1,0,0,0,0), ('wxid_removed','','已删除',1,1,1,0,0),
                ('1001@openim','','企微好友',5,1,0,0,0), ('1002@openim','','企微访客',5,0,0,0,0),
                ('1003@openim','','已删除的企微',5,1,1,0,0), ('1004@openim','','其他类型',1,1,0,0,0),
                ('1005@openim','','认证账号',5,1,0,1,0), ('unknown@other','','未知账号',5,1,0,0,0),
                ('wxid_wrong_type','','普通账号类型不符',5,1,0,0,0),
                ('r1@chatroom','','售后群',2,0,0,0,1), ('r2@chatroom','','退出的群',2,0,0,0,0);
            INSERT INTO chat_room VALUES ('r1@chatroom'), ('r2@chatroom');
        """)
        reader = SimpleNamespace(databases=[SimpleNamespace(path=self.root / "contact.db", key=b"fixture")],
                                 self_username="wxid_self")
        with patch("tools.wechat_local_bridge.reception_scope.open_snapshot", return_value=connection):
            contacts = load_contacts(reader)
        self.assertEqual({key for key, value in contacts.items() if value.selectable},
                         {"wxid_a", "1001@openim", "r1@chatroom"})
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
            client.turn_replies.return_value = []
            client.staff_replies.return_value = []
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
