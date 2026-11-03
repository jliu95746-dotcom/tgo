"""Chinese local control page and CLI for the filehelper-only bridge."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from .private_runtime import DEFAULT_RUNTIME, ensure_private_runtime, scoped_runtime
from .reader import WeChatReader
from .store import InboxStore
from .tgo_bridge import TgoBridge
from .tgo_client import TgoClient, TgoError, bridge_base_url
from .tgo_store import TgoStore
from .watch import Watcher

def account_tag(reader: WeChatReader) -> str:
    return hashlib.sha256(str(reader.root).encode()).hexdigest()[:16]


def bind_tgo(
    runtime: Path,
    base_url: str,
    platform_id: UUID,
    platform_type: str = "website",
    calibration_runtime: Path | None = None,
    *,
    scoped: bool = False,
) -> None:
    path = runtime / "tgo.private.json"
    if path.exists():
        raise ValueError("已存在客服绑定，保留原配置")
    base_url = bridge_base_url(base_url, platform_type)
    if not sys.stdin.isatty():
        raise ValueError("请在本机交互终端运行 bind-tgo")
    if calibration_runtime is not None:
        source = calibration_runtime.resolve()
        if not source.is_relative_to(DEFAULT_RUNTIME.resolve()):
            raise ValueError("只能复用本机桥接私有目录内的读取和窗口校准")
        for name in ("keys.private.json", "rpa-binding.private.json"):
            destination = runtime / name
            if not destination.exists():
                data = (source / name).read_bytes()
                with destination.open("xb") as target:
                    target.write(data)
    reader = WeChatReader(runtime / "keys.private.json")
    key = getpass.getpass("粘贴当前渠道密钥并按回车（不回显）：").strip()
    if not key:
        raise ValueError("缺少测试渠道 API Key")
    save_tgo_binding(runtime, base_url, platform_id, platform_type, reader, key, scoped=scoped)


def save_tgo_binding(
    runtime: Path, base_url: str, platform_id: UUID, platform_type: str,
    reader: WeChatReader, key: str, *, scoped: bool,
) -> None:
    path = runtime / "tgo.private.json"
    with path.open("x", encoding="utf-8") as target:
        json.dump(
            {
                "base_url": base_url,
                "platform_id": str(platform_id),
                "platform_type": platform_type,
                "platform_api_key": key,
                "chat_id": "scoped" if scoped else "filehelper",
                "account_tag": account_tag(reader),
            },
            target,
            indent=2,
        )
    print("已保存本机客服渠道绑定")


@contextmanager
def integration_lock(runtime: Path):
    import msvcrt

    with (runtime / "tgo-bridge.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("该微信客服桥接已有运行实例") from error
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def serve(
    runtime: Path,
    port: int,
    prefix: str,
    rpa_binding: Path | None = None,
    max_minutes: float = 30,
    *,
    tgo_config: Path | None = None,
    allow_send: bool = False,
    max_sends: int = 3,
) -> None:
    reader = WeChatReader(runtime / "keys.private.json")
    rpa = None
    if rpa_binding:
        from .rpa_windows import NativeRpa

        rpa = NativeRpa(rpa_binding, runtime)
    watcher: Watcher
    if tgo_config:
        if rpa is None:
            raise ValueError("客服接入需要已校准的微信窗口绑定")
        if not tgo_config.resolve().is_relative_to(runtime.resolve()):
            raise ValueError("客服凭据必须位于本机私有运行目录")
        if allow_send and rpa.send_template is None:
            raise ValueError("启用试发前需要校准发送按钮")
        client = TgoClient(tgo_config, runtime, account_tag(reader))
        ledger = TgoStore(runtime / "tgo-state.sqlite3")
        store: InboxStore = ledger
        watcher = TgoBridge(
            reader,
            ledger,
            runtime,
            prefix,
            client,
            rpa,
            allow_send,
            max_sends,
            max_minutes * 60,
        )
    else:
        store = InboxStore(runtime / "state.sqlite3")
        watcher = Watcher(reader, store, runtime, prefix, rpa, max_minutes * 60)
    watcher.initialize()
    history_count = reader.export_history(runtime / "history.private.json")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_string: str, *args: object) -> None:
            pass  # Do not put message text, URLs, or request bodies into access logs.

        def local_request(self) -> bool:
            host = self.headers.get("Host", "").split(":")[0]
            origin = self.headers.get("Origin")
            return host in ("127.0.0.1", "localhost") and (
                not origin or origin == f"http://{self.headers.get('Host')}"
            )

        def respond(
            self,
            status: int,
            body: bytes,
            content_type: str = "application/json; charset=utf-8",
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if not self.local_request():
                self.respond(403, b"{}")
                return
            path = urlparse(self.path).path
            if path == "/":
                self.respond(
                    200,
                    Path(__file__).with_name("index.html").read_bytes(),
                    "text/html; charset=utf-8",
                )
            elif path == "/app.js":
                self.respond(
                    200,
                    Path(__file__).with_name("app.js").read_bytes(),
                    "text/javascript; charset=utf-8",
                )
            elif path == "/api/status":
                with watcher.lock:
                    result = {
                        "conversation": "文件传输助手",
                        "chat_id": "filehelper",
                        "mode": "客服系统联调 · 限量试发"
                        if allow_send
                        else "只填写，不发送",
                        "key_count": len(reader.databases),
                        "history_count": history_count,
                        "watching": thread.is_alive() and not watcher.stop.is_set(),
                        "polls": watcher.polls,
                        "last_error": watcher.last_error,
                        "inbox": store.stats(),
                        "draft": store.pending_draft(),
                        "last_reply": store.latest_filled(),
                        "test_prefix": prefix,
                        "rpa_enabled": bool(rpa),
                        "rpa_status": watcher.rpa_status,
                        "rpa_evidence": json.loads(
                            (runtime / "rpa-verification.json").read_text()
                        )
                        if (runtime / "rpa-verification.json").exists()
                        else None,
                    }
                    if isinstance(watcher, TgoBridge):
                        delivery = watcher.ledger.next_delivery()
                        result["tgo"] = {
                            **watcher.client.public_status(),
                            "deliveries": watcher.ledger.delivery_stats(),
                            "sent_this_run": watcher.sent_this_run,
                            "max_sends": watcher.max_sends,
                            "draft": delivery.text if delivery else "",
                            "allow_send": watcher.allow_send,
                        }
                self.respond(200, json.dumps(result, ensure_ascii=False).encode())
            elif path == "/api/history":
                with watcher.lock:
                    messages = reader.history()
                self.respond(
                    200,
                    json.dumps(
                        [asdict(m) for m in messages], ensure_ascii=False
                    ).encode(),
                )
            elif path == "/api/proof":
                proof_file = runtime / (
                    "tgo-delivery-proof.json" if tgo_config else "rpa-verification.json"
                )
                report = (
                    json.loads(proof_file.read_text()) if proof_file.exists() else {}
                )
                self.respond(200, json.dumps(report, ensure_ascii=False).encode())
            elif path == "/api/automation":
                result = {}
                for name, filename in (
                    ("uia", "uia-capabilities.json"),
                    ("acceptance", "dynamic-acceptance.json"),
                ):
                    report_file = runtime / filename
                    result[name] = (
                        json.loads(report_file.read_text(encoding="utf-8"))
                        if report_file.exists()
                        else None
                    )
                self.respond(200, json.dumps(result, ensure_ascii=False).encode())
            else:
                self.respond(404, b"{}")

        def do_POST(self) -> None:
            if not self.local_request():
                self.respond(403, b"{}")
                return
            if self.path == "/api/stop":
                watcher.stop.set()
                self.respond(200, b'{"stopped":true}')
            else:
                self.respond(404, b"{}")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_port}"
    state = {
        "pid": os.getpid(),
        "url": url,
        "chat_id": "filehelper",
        "send_enabled": allow_send,
    }
    (runtime / "service.json").write_text(json.dumps(state), encoding="utf-8")
    thread = threading.Thread(target=watcher.run, daemon=True)
    thread.start()
    print(
        json.dumps(
            {
                "event": "ready",
                "url": url,
                "history_count": history_count,
                "test_prefix": prefix,
                "send_enabled": allow_send,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        watcher.stop.set()
        thread.join(timeout=5)
        store.close()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="个人微信本机只读采集与回复草稿验证")
    parser.add_argument(
        "--runtime", type=Path, help="本机私有运行目录"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("history", help="导出文件传输助手最近 20 条历史至私有目录")
    capture_command = sub.add_parser(
        "capture", help="对明确指定的微信主进程和账号执行只读取钥"
    )
    capture_command.add_argument("--pid", type=int, required=True)
    capture_command.add_argument("--account-root", type=Path, required=True)
    calibration = sub.add_parser("calibrate", help="框选文件传输助手窗口并回读校准草稿")
    calibration.add_argument("--pid", type=int, required=True)
    calibration.add_argument("--window-id", type=int, required=True)
    verify = sub.add_parser(
        "verify-current", help="回读当前已填写的文件传输助手草稿，并核验待办"
    )
    verify.add_argument("--rpa-binding", type=Path, required=True)
    probe = sub.add_parser("probe-uia", help="只读诊断 UIA 控件与赋值能力，不激活窗口")
    probe.add_argument("--rpa-binding", type=Path, required=True)
    bind = sub.add_parser("bind-tgo", help="在本机终端绑定专用客服测试渠道")
    bind.add_argument("--base-url", required=True, help="客服 API 根地址")
    bind.add_argument("--platform-type", choices=("website", "wechat_local"), default="website")
    bind.add_argument("--calibration-runtime", type=Path, help="复用已有本机读取和窗口校准")
    bind.add_argument(
        "--platform-id", type=UUID, required=True, help="客服测试渠道 UUID"
    )
    setup = sub.add_parser("setup-scoped", help="连接当前个人微信渠道并打开本机接待范围")
    setup.add_argument("--base-url", required=True)
    setup.add_argument("--platform-id", type=UUID, required=True)
    setup.add_argument("--account-root", type=Path)
    setup.add_argument("--preview", action="store_true", help="仅验证当前微信账号并退出")
    server = sub.add_parser("serve", help="启动仅本机可访问的控制页与新增消息监听")
    server.add_argument(
        "--port", type=int, default=0, help="监听端口，0 表示自动选择空闲端口"
    )
    server.add_argument(
        "--test-prefix",
        default="",
        help="可选测试前缀；留空时处理文件传输助手的新增文字",
    )
    server.add_argument(
        "--rpa-binding",
        type=Path,
        help="绑定已校准的文件传输助手窗口，默认填写草稿",
    )
    server.add_argument(
        "--max-minutes", type=float, default=30, help="本轮监听最长分钟数，默认 30 分钟"
    )
    server.add_argument(
        "--tgo-config", type=Path, help="本机客服测试渠道的私有绑定配置"
    )
    server.add_argument(
        "--allow-send", action="store_true", help="仅在客服联调中向文件传输助手限量发送"
    )
    server.add_argument(
        "--max-sends",
        type=int,
        choices=range(1, 11),
        default=3,
        help="本轮实际发送上限",
    )
    args = parser.parse_args()
    scoped = args.command == "setup-scoped"
    if args.runtime is None:
        args.runtime = scoped_runtime(args.platform_id) if scoped else DEFAULT_RUNTIME
    ensure_private_runtime(args.runtime, scoped=scoped)
    if args.command == "capture":
        from .memory import capture

        print(json.dumps(capture(args.pid, args.account_root, args.runtime)))
    elif args.command == "calibrate":
        from .calibration import calibrate

        calibrate(args.runtime, args.pid, args.window_id)
        print("窗口校准通过。请在微信里手动清空校准草稿后再启动桥接。")
    elif args.command == "bind-tgo":
        bind_tgo(
            args.runtime, args.base_url, args.platform_id,
            args.platform_type, args.calibration_runtime,
        )
    elif args.command == "setup-scoped":
        from .scoped_setup import setup_scoped

        try:
            with integration_lock(args.runtime):
                setup_scoped(
                    args.runtime, args.base_url, args.platform_id, args.account_root,
                    preview=args.preview,
                )
        except KeyboardInterrupt:
            raise SystemExit("已结束个人微信配置") from None
        except (ValueError, OSError, TgoError, subprocess.SubprocessError) as error:
            raise SystemExit(str(error)) from None
    elif args.command == "probe-uia":
        from .uia_probe import probe_uia

        report = probe_uia(args.rpa_binding, args.runtime)
        print(report.name)
    elif args.command == "history":
        reader = WeChatReader(args.runtime / "keys.private.json")
        count = reader.export_history(args.runtime / "history.private.json")
        print(
            json.dumps(
                {
                    "history_count": count,
                    "plaintext_output": "history.private.json",
                    "contents_printed": False,
                }
            )
        )
    elif args.command == "verify-current":
        from .rpa_windows import NativeRpa

        backend = NativeRpa(args.rpa_binding, args.runtime)
        store = InboxStore(args.runtime / "state.sqlite3")
        try:
            draft = store.pending_draft()
            if not draft:
                raise ValueError("没有待核验的回复草稿")
            backend.check_target()
            clipboard = backend.get_clipboard()
            try:
                matches = backend.read_input() == draft["reply"]
            finally:
                backend.restore_clipboard(clipboard)
            if not matches:
                raise ValueError("输入框回读未匹配待办草稿")
            backend.save_proof()
            report = {
                "chat_id": "filehelper",
                "event_id": draft["event_id"],
                "input_readback_matches": True,
                "characters": len(draft["reply"]),
                "sent": False,
            }
            (args.runtime / "rpa-verification.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            store.acknowledge(draft["event_id"])
            print(json.dumps(report, ensure_ascii=False))
        finally:
            store.close()
    else:
        if args.allow_send and not args.tgo_config:
            parser.error("--allow-send 只能和 --tgo-config 一起使用")
        with integration_lock(args.runtime) if args.tgo_config else nullcontext():
            serve(
                args.runtime,
                args.port,
                args.test_prefix,
                args.rpa_binding,
                args.max_minutes,
                tgo_config=args.tgo_config,
                allow_send=args.allow_send,
                max_sends=args.max_sends,
            )


if __name__ == "__main__":
    main()
