import json
import subprocess
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from uuid import uuid4
from xml.etree import ElementTree as ET

from tools.wechat_local_bridge import resident, scoped_setup
from tools.wechat_local_bridge.rpa import RpaNotReady


class ResidentBridgeTests(unittest.TestCase):
    def test_logon_task_runs_without_time_limit_or_another_console(self):
        sid = "S-1-5-21-1-2-3-1000"
        program = Path("tool with spaces") / ".venv" / "Scripts" / "pythonw.exe"
        runtime = Path("channel with spaces")
        channel = uuid4()
        base = "https://customer.example/api"
        root = ET.fromstring(resident.task_definition(sid, program, runtime, base, channel))
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

        def value(path):
            return root.findtext("/".join("t:" + element for element in path.split("/")), namespaces=ns)

        self.assertEqual(value("Triggers/LogonTrigger/UserId"), sid)
        self.assertEqual(value("Principals/Principal/UserId"), sid)
        self.assertEqual(value("Principals/Principal/LogonType"), "InteractiveToken")
        self.assertEqual(value("Principals/Principal/RunLevel"), "LeastPrivilege")
        self.assertEqual(value("Settings/MultipleInstancesPolicy"), "IgnoreNew")
        self.assertEqual(value("Settings/ExecutionTimeLimit"), "PT0S")
        self.assertEqual(value("Settings/RestartOnFailure/Interval"), "PT1M")
        self.assertEqual(value("Settings/StopIfGoingOnBatteries"), "false")
        self.assertEqual(value("Actions/Exec/Command"), str(program))
        self.assertEqual(value("Actions/Exec/Arguments"), subprocess.list2cmdline([
            "-m", "tools.wechat_local_bridge.cli", "--runtime", str(runtime.resolve()),
            "setup-scoped", "--base-url", base, "--platform-id", str(channel), "--resident",
        ]))

    def test_first_setup_starts_task_after_releasing_the_channel_lock(self):
        actions = []
        channel = uuid4()
        runtime = Path("runtime")
        base = "https://customer.example/api"
        scope_url = "http://127.0.0.1:7415/?local=test"

        @contextmanager
        def lock(path):
            self.assertEqual(path, runtime)
            actions.append("lock")
            yield
            actions.append("unlock")

        with (
            patch.object(resident, "integration_lock", lock),
            patch.object(resident, "prepare_scoped", return_value=object()),
            patch.object(resident, "register_autostart", return_value=lambda: actions.append("start")) as register,
            patch.object(resident, "wait_for_scope", return_value=scope_url),
            patch.object(resident, "open_scope") as open_scope,
            patch("builtins.print"),
        ):
            resident.configure_scoped(runtime, base, channel, None, preview=False)
        register.assert_called_once_with(runtime, base, channel)
        self.assertEqual(actions, ["lock", "unlock", "start"])
        open_scope.assert_called_once_with(scope_url)

    def test_denied_task_registration_falls_back_to_current_users_startup_folder(self):
        channel = uuid4()
        runtime = Path("runtime")
        base = "https://customer.example/api"
        shortcut = Path("Startup") / f"Yujian-WeChatBridge-{channel}.lnk"
        with (
            patch.object(Path, "is_file", return_value=True),
            patch.object(resident, "register_task", side_effect=PermissionError),
            patch.object(resident, "register_startup_shortcut", return_value=shortcut) as register,
            patch.object(resident.os, "startfile", create=True) as launch,
        ):
            start = resident.register_autostart(runtime, base, channel)
            launch.assert_not_called()
            start()
        program, *arguments = register.call_args.args
        self.assertEqual(program.name, "pythonw.exe")
        self.assertEqual(arguments, [runtime, base, channel])
        launch.assert_called_once_with(str(shortcut))

    def test_preview_never_registers_a_task(self):
        with (
            patch.object(resident, "integration_lock"),
            patch.object(resident, "prepare_scoped", return_value=None),
            patch.object(resident, "register_autostart") as register,
        ):
            resident.configure_scoped(Path("runtime"), "https://customer.example/api", uuid4(), None, preview=True)
        register.assert_not_called()

    def test_reopening_the_same_channel_reuses_its_scope_server(self):
        channel = uuid4()
        base = "https://customer.example/api"
        url = "http://127.0.0.1:7415/?local=test"
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "tgo.private.json").write_text(json.dumps({
                "platform_id": str(channel), "base_url": base, "chat_id": "scoped",
            }), encoding="utf-8")
            with (
                patch.object(resident, "integration_lock", side_effect=resident.BridgeAlreadyRunning),
                patch.object(resident, "active_scope_url", return_value=url),
                patch.object(resident, "open_scope") as open_scope,
                patch.object(resident, "register_autostart") as register,
            ):
                resident.configure_scoped(runtime, base, channel, None, preview=False)
                with self.assertRaises(resident.BridgeAlreadyRunning):
                    resident.configure_scoped(runtime, base, uuid4(), None, preview=False)
            open_scope.assert_called_once_with(url)
            register.assert_not_called()

    def test_resident_waits_and_reopens_the_saved_channel_without_pairing(self):
        channel = uuid4()
        base = "https://customer.example/api"
        first, second = object(), object()
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            with (
                patch.object(resident, "integration_lock"),
                patch.object(resident, "prepare_scoped", side_effect=[RpaNotReady(), first, second, None]) as prepare,
                patch.object(resident, "serve_scoped") as serve,
                patch.object(resident.time, "sleep") as sleep,
            ):
                resident.run_resident(runtime, base, channel)

        self.assertEqual(prepare.call_count, 4)
        for invocation in prepare.call_args_list:
            self.assertEqual(invocation.args, (runtime, base, channel, None))
            self.assertEqual(invocation.kwargs, {"bound_only": True})
        self.assertEqual([call.args for call in serve.call_args_list], [(first,), (second,)])
        self.assertTrue(all(call.kwargs == {"resident": True} for call in serve.call_args_list))
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [8, 3, 3])

    def test_scope_handoff_rejects_external_and_stale_urls(self):
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            state = runtime / "scope-control.private.json"
            with patch.object(resident, "build_opener") as build:
                state.write_text(json.dumps({"url": "http://example.com/?local=test"}), encoding="utf-8")
                self.assertIsNone(resident.active_scope_url(runtime))
                build.assert_not_called()

                url = "http://127.0.0.1:7415/?local=test"
                state.write_text(json.dumps({"url": url}), encoding="utf-8")
                opener = build.return_value
                opener.open.side_effect = OSError("connection refused")
                self.assertIsNone(resident.active_scope_url(runtime))

                opener.open.side_effect = None
                response = opener.open.return_value.__enter__.return_value
                response.read.return_value = "<title>个人微信接待范围</title>".encode()
                self.assertEqual(resident.active_scope_url(runtime), url)

    def test_background_scope_server_does_not_open_browser_and_stops_on_window_loss(self):
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            stop, window_lost = threading.Event(), threading.Event()
            bridge = Mock(runtime=runtime, stop=stop, desktop_lock=threading.Lock())
            bridge.run.side_effect = stop.wait

            def validate_window():
                if window_lost.is_set():
                    raise RpaNotReady("微信已退出")

            bridge.window.validate.side_effect = validate_window
            with (
                patch("tools.wechat_local_bridge.window_ocr.check_ocr"),
                patch("tools.wechat_local_bridge.scoped_rpa.check_window") as foreground_check,
                patch.object(scoped_setup.webbrowser, "open") as open_browser,
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                server = executor.submit(scoped_setup.serve_scoped, bridge, resident=True)
                try:
                    for _ in range(60):
                        if url := resident.active_scope_url(runtime):
                            break
                        if server.done():
                            server.result()
                        threading.Event().wait(0.05)
                    else:
                        self.fail("本机接待页未启动")
                    self.assertTrue(url.startswith("http://127.0.0.1:"))
                finally:
                    window_lost.set()
                server.result(timeout=8)
            open_browser.assert_not_called()
            foreground_check.assert_not_called()
            self.assertFalse((runtime / "scope-control.private.json").exists())
