import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from test_crypto import KEY, SALT, encrypted_page

from tools.wechat_local_bridge import memory
from tools.wechat_local_bridge import scoped_setup


class AccountDiscoveryTests(unittest.TestCase):
    def test_reads_custom_location_and_ignores_unrelated_config(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / "appdata" / "Tencent" / "xwechat" / "config"
            config.mkdir(parents=True)
            custom = home / "微信数据"
            account = custom / "xwechat_files" / "test_account"
            (account / "db_storage").mkdir(parents=True)
            (config / "path.ini").write_text(str(custom), encoding="utf-8")
            (config / "duplicate.ini").write_text(str(custom / "xwechat_files"), encoding="utf-16")
            (config / "unrelated.ini").write_text("[section]\npath=unknown", encoding="utf-8")
            with patch.dict(os.environ, {"APPDATA": str(home / "appdata")}):
                self.assertEqual(scoped_setup.find_accounts(home / "Documents"), (account.resolve(),))

    def test_current_process_keys_select_only_one_account(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            active, inactive, runtime = home / "active", home / "inactive", home / "runtime"
            for account, page in (
                (active, encrypted_page(1)),
                (inactive, SALT + bytes(4096 - len(SALT))),
            ):
                storage = account / "db_storage"
                storage.mkdir(parents=True)
                for database in ("contact.db", "session.db", "message_0.db"):
                    (storage / database).write_bytes(page)

            def scan(_pid, _regions, _read_region, _read_mem, _files, _salts, keys, _remaining, _print):
                keys[SALT.hex()] = KEY.hex()
                return {"candidate_count": 1}

            with (
                patch.object(memory, "ReadOnlyMemory") as read_memory,
                patch.object(memory.scanner, "_scan_windows_v411_config_cipher", side_effect=scan) as read,
                patch.object(memory, "save_keys", wraps=memory.save_keys) as save,
            ):
                read_memory.return_value.read_bytes = 4096
                memory.capture_scoped_account(123, (inactive, active), runtime)
                self.assertEqual(read.call_count, 1)
                databases = save.call_args.args[2]
                self.assertEqual(len(databases), 3)
                self.assertTrue(all(Path(item["path"]).is_relative_to(active) for item in databases))

                (inactive / "db_storage" / "session.db").write_bytes(encrypted_page(1))
                previous_keys = (runtime / "keys.private.json").read_bytes()
                with self.assertRaisesRegex(ValueError, "多个微信账号"):
                    memory.capture_scoped_account(123, (inactive, active), runtime)
                self.assertEqual((runtime / "keys.private.json").read_bytes(), previous_keys)
                self.assertEqual(save.call_count, 1)


    def test_preview_verifies_account_and_exits_without_pairing_or_saved_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            account = runtime.parent / "fixture_account"

            def capture(_pid, accounts, destination):
                self.assertEqual(accounts, (account,))
                (destination / "keys.private.json").write_text("fixture", encoding="utf-8")

            with (
                patch.object(scoped_setup, "discover_window", return_value=(123, 456, runtime)),
                patch.object(scoped_setup, "find_accounts", return_value=(account,)),
                patch.object(scoped_setup, "capture_scoped_account", side_effect=capture) as verify,
                patch.object(scoped_setup, "serve_scoped") as serve,
                patch("builtins.print") as output,
            ):
                self.assertIsNone(scoped_setup.prepare_scoped(runtime, "http://localhost", uuid4(), None, preview=True))
            verify.assert_called_once()
            serve.assert_not_called()
            self.assertIn("试跑通过", output.call_args.args[0])
            self.assertEqual(list(runtime.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
