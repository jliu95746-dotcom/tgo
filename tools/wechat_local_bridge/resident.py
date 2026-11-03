"""Start the bound bridge in the current Windows user's desktop session."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from functools import partial
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from uuid import UUID
from xml.etree import ElementTree as ET

from .private_runtime import BridgeAlreadyRunning, current_user_sid, integration_lock
from .rpa import RpaNotReady
from .scoped_setup import prepare_scoped, serve_scoped
from .tgo_client import TgoError, bridge_base_url


def bridge_arguments(runtime: Path, base: str, channel: UUID) -> str:
    return subprocess.list2cmdline([
        "-m", "tools.wechat_local_bridge.cli", "--runtime", str(runtime.resolve()),
        "setup-scoped", "--base-url", base, "--platform-id", str(channel), "--resident",
    ])


def task_definition(sid: str, program: Path, runtime: Path, base: str, channel: UUID) -> bytes:
    task = ET.Element("Task", version="1.3", xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task")
    trigger = ET.SubElement(ET.SubElement(task, "Triggers"), "LogonTrigger")
    ET.SubElement(trigger, "UserId").text = sid

    principal = ET.SubElement(ET.SubElement(task, "Principals"), "Principal", id="BridgeUser")
    ET.SubElement(principal, "UserId").text = sid
    ET.SubElement(principal, "LogonType").text = "InteractiveToken"
    ET.SubElement(principal, "RunLevel").text = "LeastPrivilege"

    settings = ET.SubElement(task, "Settings")
    for key, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("AllowStartOnDemand", "true"),
        ("ExecutionTimeLimit", "PT0S"),
    ):
        ET.SubElement(settings, key).text = value
    restart = ET.SubElement(settings, "RestartOnFailure")
    ET.SubElement(restart, "Interval").text = "PT1M"
    ET.SubElement(restart, "Count").text = "5"

    action = ET.SubElement(ET.SubElement(task, "Actions", Context="BridgeUser"), "Exec")
    ET.SubElement(action, "Command").text = str(program)
    ET.SubElement(action, "Arguments").text = bridge_arguments(runtime, base, channel)
    ET.SubElement(action, "WorkingDirectory").text = str(Path(__file__).resolve().parents[2])
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def scheduler(*arguments: str) -> None:
    subprocess.run(
        ["schtasks.exe", *arguments], capture_output=True, check=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def register_task(program: Path, runtime: Path, base: str, channel: UUID) -> str:
    sid = current_user_sid()
    name = f"Yujian-WeChatBridge-{sid}-{channel}"
    with TemporaryDirectory() as temporary:
        definition = Path(temporary) / "bridge.xml"
        definition.write_bytes(task_definition(sid, program, runtime, base, channel))
        scheduler("/Create", "/TN", name, "/XML", str(definition), "/F")
    return name


def register_startup_shortcut(program: Path, runtime: Path, base: str, channel: UUID) -> Path:
    from comtypes import COMError
    from comtypes.client import CreateObject

    try:
        shell = CreateObject("WScript.Shell", dynamic=True)
        startup = Path(shell.SpecialFolders.Item("Startup"))
        path = startup / f"Yujian-WeChatBridge-{channel}.lnk"
        shortcut = shell.CreateShortcut(str(path))
        shortcut.TargetPath = str(program)
        shortcut.Arguments = bridge_arguments(runtime, base, channel)
        shortcut.WorkingDirectory = str(Path(__file__).resolve().parents[2])
        shortcut.Description = "域见客服个人微信桥接"
        shortcut.Save()
    except COMError as error:
        raise OSError("Windows 无法保存个人微信桥接启动快捷方式") from error
    if not path.is_file():
        raise OSError("Windows 启动快捷方式未保存")
    return path


def register_autostart(runtime: Path, base: str, channel: UUID) -> Callable[[], None]:
    program = Path(sys.executable).with_name("pythonw.exe")
    if not program.is_file():
        raise FileNotFoundError("当前 Python 环境缺少 pythonw.exe")
    try:
        task = register_task(program, runtime, base, channel)
    except (OSError, subprocess.SubprocessError):
        shortcut = register_startup_shortcut(program, runtime, base, channel)
        return partial(os.startfile, str(shortcut))
    return partial(scheduler, "/Run", "/TN", task)


class LocalOnlyRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def active_scope_url(runtime: Path) -> str | None:
    try:
        url = json.loads((runtime / "scope-control.private.json").read_text(encoding="utf-8"))["url"]
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.netloc != f"127.0.0.1:{parsed.port}"
            or parsed.port is None
            or parsed.path != "/"
            or parsed.fragment
            or not parse_qs(parsed.query).get("local")
        ):
            return None
        opener = build_opener(ProxyHandler({}), LocalOnlyRedirect())
        with opener.open(url, timeout=1) as response:
            if "<title>个人微信接待范围</title>".encode() in response.read(1024):
                return url
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def wait_for_scope(runtime: Path) -> str | None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if url := active_scope_url(runtime):
            return url
        time.sleep(0.5)
    return None


def open_scope(url: str) -> None:
    if webbrowser.open(url):
        print("已打开本机接待范围。请勾选联系人或群聊并保存。", flush=True)
    else:
        print("桥接已启动，浏览器暂未打开。可以再次运行启动命令打开接待范围。", flush=True)


def configure_scoped(
    runtime: Path, base: str, channel: UUID, account_root: Path | None, *, preview: bool
) -> None:
    base = bridge_base_url(base, "wechat_local")
    try:
        with integration_lock(runtime):
            bridge = prepare_scoped(runtime, base, channel, account_root, preview=preview)
            if bridge is None:
                return
            try:
                start = register_autostart(runtime, base, channel)
            except (OSError, subprocess.SubprocessError):
                print("Windows 自动启动设置失败，本次将通过当前窗口运行。", flush=True)
                serve_scoped(bridge)
                return
    except BridgeAlreadyRunning:
        if not preview:
            binding = runtime / "tgo.private.json"
            if binding.is_file():
                saved = json.loads(binding.read_text(encoding="utf-8"))
                if (saved.get("platform_id"), saved.get("base_url"), saved.get("chat_id")) == (
                    str(channel), base, "scoped"
                ) and (url := active_scope_url(runtime)):
                    open_scope(url)
                    return
        raise

    try:
        start()
    except (OSError, subprocess.SubprocessError):
        print("已设置 Windows 登录后自动启动，本次通过当前窗口运行。", flush=True)
        with integration_lock(runtime):
            serve_scoped(bridge)
        return

    print("已设置 Windows 登录后自动启动。", flush=True)
    if url := wait_for_scope(runtime):
        open_scope(url)
    else:
        print("已请求后台启动。微信就绪后，可再次运行启动命令打开接待范围。", flush=True)


def run_resident(runtime: Path, base: str, channel: UUID) -> None:
    logger = logging.getLogger(__name__)
    handler = RotatingFileHandler(runtime / "resident.log", maxBytes=128 * 1024, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with integration_lock(runtime):
            logger.info("后台启动")
            waiting = None
            while True:
                try:
                    bridge = prepare_scoped(runtime, base, channel, None, bound_only=True)
                except (ValueError, OSError, RpaNotReady, TgoError, subprocess.SubprocessError, sqlite3.Error) as error:
                    reason = type(error).__name__
                    if waiting != reason:
                        logger.info("等待微信或桥接就绪 (%s)", reason)
                        waiting = reason
                    time.sleep(8)
                    continue
                if bridge is None:
                    logger.info("当前渠道未绑定，结束运行")
                    return
                waiting = None
                logger.info("微信已核验，启动接待监听")
                serve_scoped(bridge, resident=True)
                logger.info("微信窗口已变化，等待重新识别")
                time.sleep(3)
    except Exception as error:
        logger.error("桥接退出 (%s)", type(error).__name__)
        raise
    finally:
        logger.removeHandler(handler)
        handler.close()
