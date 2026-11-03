"""Transfer a single-use browser confirmation to this Windows bridge."""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Full, Queue
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener
from uuid import UUID

from .tgo_client import NoRedirect, TgoError, post_json


def pair_current_channel(base: str, platform_id: UUID) -> str:
    verifier = secrets.token_urlsafe(32)
    challenge = hashlib.sha256(verifier.encode("ascii")).hexdigest()
    pending: Queue[str] = Queue(maxsize=1)

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def do_GET(self) -> None:
            target = urlsplit(self.path)
            query = parse_qs(target.query)
            code = query.get("code", [""])[0]
            if (
                self.headers.get("Host") != f"127.0.0.1:{port}"
                or target.path != "/wechat-pair"
                or query.get("state") != [challenge]
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", code)
            ):
                self.send_error(400)
                return
            try:
                pending.put_nowait(code)
            except Full:
                self.send_error(409)
                return
            body = "授权已交给本机工具，请查看工具中的连接结果。".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Callback) as server:
        port = server.server_address[1]
        fragment = urlencode({"wechat_pair": challenge, "port": port})
        dashboard = urlsplit(base)._replace(path=f"/platforms/{platform_id}", fragment=fragment).geturl()
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            try:
                opened = webbrowser.open(dashboard)
            except (OSError, webbrowser.Error):
                opened = False
            print("请在这台电脑的客服后台登录，并点「连接这台电脑」。", flush=True)
            if not opened:
                print("请在这台电脑的浏览器打开 " + dashboard, flush=True)
            try:
                code = pending.get(timeout=300)
            except Empty:
                raise TgoError("等待连接确认超时，请重新启动工具") from None
        finally:
            server.shutdown()
            worker.join()

    endpoint = f"{base}/v1/platforms/{platform_id}/wechat-local/pair/exchange"
    reply = post_json(build_opener(ProxyHandler({}), NoRedirect()), endpoint,
                      {"code": code, "verifier": verifier}, {"Content-Type": "application/json"})
    key = reply.get("platform_api_key")
    if not isinstance(key, str) or not key:
        raise TgoError("后台没有返回有效连接凭据，请重新启动工具")
    return key
