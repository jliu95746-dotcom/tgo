"""Interactive setup and loopback-only reception scope controls."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
import time
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from .cli import account_tag, save_tgo_binding
from .pairing import pair_current_channel
from .memory import capture_scoped_account
from .reader import WeChatReader
from .reception_scope import Scope, ScopeSettings
from .rpa import RpaNotReady
from .scoped_bridge import ScopedBridge
from .tgo_client import TgoClient, TgoError, TgoUnavailable, bridge_base_url
from .window_control import WindowControl


def discover_window() -> tuple[int, int, Path]:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
         "$w=@(Get-Process Weixin -ErrorAction SilentlyContinue | Where-Object { [long]$_.MainWindowHandle -ne 0 } | "
         "ForEach-Object { @{pid=$_.Id;hwnd=[long]$_.MainWindowHandle} }); "
         "@{windows=$w;documents=[Environment]::GetFolderPath('MyDocuments')} | ConvertTo-Json -Compress"],
        capture_output=True, check=True, timeout=12,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    data = json.loads(result.stdout.decode("utf-8-sig"))
    windows = data["windows"]
    if len(windows) != 1:
        raise ValueError("请登录 Windows 微信，并保留一个可识别的微信主窗口")
    pid, hwnd = windows[0]["pid"], windows[0]["hwnd"]
    WindowControl(hwnd, pid).validate()
    return pid, hwnd, Path(data["documents"])


def find_accounts(documents: Path) -> tuple[Path, ...]:
    roots = {documents / "xwechat_files"}
    if appdata := os.environ.get("APPDATA"):
        config = Path(appdata) / "Tencent" / "xwechat" / "config"
        for file in config.glob("*.ini"):
            try:
                with file.open("rb") as stream:
                    data = stream.read(4097)
            except OSError:
                continue
            if len(data) > 4096:
                continue
            encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            try:
                value = data.decode(encoding).strip().strip("\0")
            except UnicodeError:
                continue
            hint = Path(value)
            if "\0" not in value and hint.is_absolute() and not hint.drive.startswith("\\"):
                roots.add(hint if hint.name.casefold() == "xwechat_files" else hint / "xwechat_files")

    return tuple(sorted({
        path.resolve()
        for root in roots
        for path in root.glob("*")
        if (path / "db_storage").is_dir()
    }))


def prepare_scoped(
    runtime: Path, base: str, platform_id: UUID, account_root: Path | None, *,
    preview: bool = False, bound_only: bool = False,
) -> ScopedBridge | None:
    base = bridge_base_url(base, "wechat_local")
    binding = runtime / "tgo.private.json"
    if bound_only and not binding.is_file():
        return None
    pid, hwnd, documents = discover_window()
    key_file = runtime / "keys.private.json"
    if not preview and binding.exists():
        existing = json.loads(binding.read_text(encoding="utf-8"))
        if existing.get("platform_id") != str(platform_id) or existing.get("base_url") != base or existing.get("chat_id") != "scoped":
            raise ValueError("运行目录已绑定其他渠道，请使用后台提供的当前渠道命令")
        bound_account = WeChatReader(key_file).root.parent
        if account_root and account_root.resolve() != bound_account:
            raise ValueError("当前渠道已绑定另一个本机微信账号")
        account_root = bound_account
    accounts = (account_root,) if account_root else find_accounts(documents)
    if not accounts:
        raise ValueError("未找到微信数据。请在已登录的微信中打开任意会话，再重新运行工具")
    if preview:
        with TemporaryDirectory(dir=runtime, prefix="preview-") as temporary:
            capture_scoped_account(pid, accounts, Path(temporary))
        print("试跑通过。已自动识别并核验当前登录的微信账号。", flush=True)
        return None

    capture_scoped_account(pid, accounts, runtime)
    reader = WeChatReader(key_file)
    new_binding = not binding.exists()
    if bound_only and new_binding:
        return None
    if new_binding:
        key = pair_current_channel(base, platform_id)
        save_tgo_binding(runtime, base, platform_id, "wechat_local", reader, key, scoped=True)
    scope = Scope(reader, runtime / "reception-scope.private.json")
    client = TgoClient(binding, runtime, account_tag(reader), scoped=True)
    if new_binding:
        try:
            client.publish_scope([])
        except (TgoError, TgoUnavailable):
            binding.unlink()
            raise
    return ScopedBridge(scope, client, runtime, allow_send=True, window=WindowControl(hwnd, pid))


def serve_scoped(bridge: ScopedBridge, *, resident: bool = False) -> None:
    token = secrets.token_urlsafe(32)
    checking = threading.Lock()
    window_status = "等待检查微信窗口"
    window = bridge.window
    channel = UUID(bridge.client.platform_id)
    base = urlsplit(bridge.client.base)
    host = base.netloc.lower()
    default_port = ":443" if base.scheme == "https" else ":80"
    dashboard_origin = f"{base.scheme}://{host.removesuffix(default_port)}"
    entry_path = f"/dashboard/reception/{channel}"

    def check(passive: bool) -> None:
        nonlocal window_status
        try:
            from .scoped_rpa import check_window

            with bridge.desktop_lock:
                bridge.window_ready = False
                if window is None:
                    raise RpaNotReady("微信窗口暂不可用")
                if passive:
                    from .window_ocr import check_ocr

                    window.validate()
                    check_ocr()
                else:
                    check_window(window)
                bridge.window_ready = True
                window_status = "已识别微信窗口，发送时会重新核对" if passive else "已识别微信窗口"
        except (OSError, ValueError, RpaNotReady) as exc:
            window_status = str(exc)
        except Exception as exc:
            window_status = f"窗口检查暂停（{type(exc).__name__}）"
        finally:
            checking.release()

    def start_check(*, passive: bool = False) -> None:
        if checking.acquire(blocking=False):
            threading.Thread(target=check, args=(passive,), daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def allowed(self, *, page: bool = False) -> bool:
            host = self.headers.get("Host")
            origin = self.headers.get("Origin")
            supplied = parse_qs(urlsplit(self.path).query).get("local", [""])[0] if page else self.headers.get("X-Bridge-Local-Key", "")
            return (
                host == f"127.0.0.1:{self.server.server_port}"
                and (not origin or origin == f"http://{host}")
                and secrets.compare_digest(supplied, token)
            )

        def respond(
            self, status: int, value: object, media: str = "application/json", *,
            dashboard: bool = False,
        ) -> None:
            content = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", media + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(content)))
            if dashboard:
                self.send_header("Access-Control-Allow-Origin", dashboard_origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(content)

        def dashboard_entry(self, *, open_page: bool = False) -> None:
            if (
                self.headers.get("Origin") != dashboard_origin
                or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
            ):
                self.respond(403, {})
                return
            try:
                if window is None or bridge.stop.is_set() or not bridge.scope.ready:
                    raise RpaNotReady("接待配置尚未就绪")
                window.validate()
            except RpaNotReady:
                self.respond(503, {}, dashboard=True)
                return
            if open_page:
                self.respond(200, {"url": url}, dashboard=True)
            else:
                self.respond(200, {"ready": True}, dashboard=True)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if self.path == entry_path:
                self.dashboard_entry()
            elif path == "/reception.js":
                self.respond(200, Path(__file__).with_name("reception.js").read_bytes(), "text/javascript")
            elif path == "/" and self.allowed(page=True):
                self.respond(200, Path(__file__).with_name("reception.html").read_bytes(), "text/html")
            elif path == "/api/scope" and self.allowed():
                with bridge.scope.lock:
                    scope = bridge.scope
                    self.respond(200, {
                        "ready": scope.ready, "error": scope.error or bridge.error,
                        "settings": asdict(scope.settings), "suspended": sorted(scope.suspended),
                        "window_ready": bridge.window_ready, "window_checking": checking.locked(),
                        "window_status": window_status,
                        "contacts": [asdict(contact) for contact in scope.contacts.values() if contact.selectable],
                        "delivery": bridge.status_snapshot,
                    })
            else:
                self.respond(403, {})

        def do_POST(self) -> None:
            if self.path == entry_path:
                self.dashboard_entry(open_page=True)
                return
            if not self.allowed():
                self.respond(403, {})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 256_000:
                    raise ValueError("接待配置大小无效")
                body = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("接待配置格式无效")
                if self.path == "/api/scope":
                    resume = body.get("resume", [])
                    if not isinstance(resume, list) or any(not isinstance(item, str) for item in resume):
                        raise ValueError("恢复列表无效")
                    bridge.scope.save(ScopeSettings.parse(body.get("settings")), resume)
                elif self.path == "/api/refresh":
                    bridge.scope.refresh(force=True)
                elif self.path == "/api/window-check":
                    start_check()
                else:
                    self.respond(404, {})
                    return
                self.respond(200, {"ok": True})
            except (ValueError, OSError) as exc:
                self.respond(400, {"error": str(exc)})

    # Probe the same bounded set from the dashboard. Leave the local CLI usable if all are taken.
    port_seed = channel.int & 0xFFFFFF
    for offset in (0, 3571, 7142):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 38000 + (port_seed + offset) % 10000), Handler)
            break
        except OSError:
            continue
    else:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/?local={token}"
    worker = threading.Thread(target=bridge.run, daemon=True)
    control = bridge.runtime / "scope-control.private.json"
    start_check(passive=resident)
    worker.start()
    try:
        control.write_text(json.dumps({"url": url}), encoding="utf-8")
        if not resident:
            webbrowser.open(url)
            print("已打开本机接待范围。请勾选联系人或群聊并保存。", flush=True)
            server.serve_forever(poll_interval=0.5)
            return

        server.timeout = 1
        next_window_check = 0.0
        while worker.is_alive():
            server.handle_request()
            now = time.monotonic()
            if now >= next_window_check:
                try:
                    if window is None:
                        return
                    window.validate()
                except RpaNotReady:
                    return
                next_window_check = now + 5
        raise RuntimeError("个人微信接待线程已停止")
    finally:
        bridge.stop.set()
        worker.join(timeout=20)
        server.server_close()
        control.unlink(missing_ok=True)
        if worker.is_alive():
            raise RuntimeError("接待线程尚未退出")
