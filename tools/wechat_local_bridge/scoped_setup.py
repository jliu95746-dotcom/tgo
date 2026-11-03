"""Interactive setup and loopback-only reception scope controls."""

from __future__ import annotations

import json
import secrets
import subprocess
import threading
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from .cli import account_tag, bind_tgo
from .memory import capture
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
    )
    data = json.loads(result.stdout.decode("utf-8-sig"))
    windows = data["windows"]
    if len(windows) != 1:
        raise ValueError("请登录 Windows 微信，并保留一个可识别的微信主窗口")
    pid, hwnd = windows[0]["pid"], windows[0]["hwnd"]
    WindowControl(hwnd, pid).validate()
    return pid, hwnd, Path(data["documents"])


def choose_account(documents: Path) -> Path:
    root = documents / "xwechat_files"
    candidates = [path for path in root.glob("*") if (path / "db_storage").is_dir()]
    if len(candidates) == 1:
        return candidates[0]
    import tkinter as tk
    from tkinter.filedialog import askdirectory

    window = tk.Tk()
    window.withdraw()
    try:
        selected = askdirectory(parent=window, title="选择含 db_storage 的当前微信账号目录",
                                initialdir=str(root if root.is_dir() else documents), mustexist=True)
    finally:
        window.destroy()
    if not selected or not (Path(selected) / "db_storage").is_dir():
        raise ValueError("请选择含 db_storage 的当前微信账号目录")
    return Path(selected)


def setup_scoped(runtime: Path, base: str, platform_id: UUID, account_root: Path | None) -> None:
    base = bridge_base_url(base, "wechat_local")
    pid, hwnd, documents = discover_window()
    binding = runtime / "tgo.private.json"
    key_file = runtime / "keys.private.json"
    if binding.exists():
        existing = json.loads(binding.read_text(encoding="utf-8"))
        if existing.get("platform_id") != str(platform_id) or existing.get("base_url") != base or existing.get("chat_id") != "scoped":
            raise ValueError("运行目录已绑定其他渠道，请使用后台提供的当前渠道命令")
        bound_account = WeChatReader(key_file).root.parent
        if account_root and account_root.resolve() != bound_account:
            raise ValueError("当前渠道已绑定另一个本机微信账号")
        account_root = bound_account
    capture(pid, account_root or choose_account(documents), runtime)
    new_binding = not binding.exists()
    if new_binding:
        bind_tgo(runtime, base, platform_id, "wechat_local", scoped=True)
    reader = WeChatReader(key_file)
    scope = Scope(reader, runtime / "reception-scope.private.json")
    client = TgoClient(binding, runtime, account_tag(reader), scoped=True)
    if new_binding:
        try:
            client.publish_scope([])
        except (TgoError, TgoUnavailable):
            binding.unlink()
            raise
    serve_scoped(ScopedBridge(scope, client, runtime, allow_send=True, window=WindowControl(hwnd, pid)))


def serve_scoped(bridge: ScopedBridge) -> None:
    token = secrets.token_urlsafe(32)
    checking = threading.Lock()
    window_status = "等待检查微信窗口"

    def check() -> None:
        nonlocal window_status
        try:
            from .scoped_rpa import check_window

            with bridge.desktop_lock:
                bridge.window_ready = False
                if bridge.window is None:
                    raise RpaNotReady("微信窗口暂不可用")
                check_window(bridge.window)
                bridge.window_ready = True
                window_status = "已识别微信窗口"
        except (OSError, ValueError, RpaNotReady) as exc:
            window_status = str(exc)
        except Exception as exc:
            window_status = f"窗口检查暂停（{type(exc).__name__}）"
        finally:
            checking.release()

    def start_check() -> None:
        if checking.acquire(blocking=False):
            threading.Thread(target=check, daemon=True).start()

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

        def respond(self, status: int, value: object, media: str = "application/json") -> None:
            content = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", media + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/reception.js":
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

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/?local={token}"
    worker = threading.Thread(target=bridge.run, daemon=True)
    start_check()
    worker.start()
    (bridge.runtime / "scope-control.private.json").write_text(json.dumps({"url": url}), encoding="utf-8")
    webbrowser.open(url)
    print("已打开本机接待范围。请勾选联系人或群聊并保存。", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        bridge.stop.set()
        worker.join(timeout=20)
        server.server_close()
