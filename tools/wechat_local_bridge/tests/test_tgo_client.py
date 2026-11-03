import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.wechat_local_bridge.tgo_client import TgoClient, TgoError, bridge_base_url


class TgoClientTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        calls = self.calls = []
        heartbeat_headers = self.heartbeat_headers = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, self.headers.get("Authorization"), body))
                if self.path.endswith("register"):
                    status, result = (
                        201,
                        {
                            "id": "visitor",
                            "platform_id": "test-platform",
                            "channel_id": "visitor-vtr",
                            "channel_type": 251,
                            "platform_open_id": body["platform_open_id"],
                            "im_token": "fake-im",
                            "visitor_access_token": "fake-access",
                        },
                    )
                elif self.path.endswith("sync"):
                    status, result = 200, {"messages": []}
                elif self.path.endswith("/heartbeat"):
                    heartbeat_headers.append(self.headers.get("X-Platform-API-Key"))
                    status, result = 200, {"connected": True}
                elif body.get("message") == "redirect":
                    self.send_response(307)
                    self.send_header("Location", "/unexpected")
                    self.end_headers()
                    return
                else:
                    status, result = (
                        200,
                        {
                            "success": True,
                            "event_type": "accepted",
                            "visitor_id": "visitor",
                        },
                    )
                data = json.dumps(result).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.config_file = self.root / "config.json"
        self.config_file.write_text(
            json.dumps(
                {
                    "base_url": f"http://127.0.0.1:{server.server_port}",
                    "chat_id": "filehelper",
                    "platform_api_key": "test-channel-key",
                    "platform_id": "test-platform",
                    "account_tag": "account-a",
                }
            ),
            encoding="utf-8",
        )
        self.client = TgoClient(self.config_file, self.root, "account-a")

    def test_visit_and_retry_use_same_narrow_identity_and_source_id(self):
        self.client.connect()
        self.client.submit("wechat-22", "测试问题")
        self.client.submit("wechat-22", "测试问题")
        self.assertEqual(self.calls[0][0], "/v1/visitors/register")
        self.assertIsNone(self.calls[0][1])
        self.assertEqual(self.calls[1][1], "Bearer fake-im")
        self.assertEqual(self.calls[-1], self.calls[-2])
        self.assertEqual(self.calls[-1][2]["source_message_id"], "wxlocal:wechat-22")
        self.assertEqual(self.calls[-1][2]["channel_id"], "visitor-vtr")

    def test_redirect_does_not_forward_visitor_credentials(self):
        self.client.connect()
        with self.assertRaisesRegex(TgoError, "HTTP 307"):
            self.client.submit("wx", "redirect")
        self.assertNotIn("/unexpected", [request[0] for request in self.calls])

    def test_first_class_platform_posts_signed_heartbeat(self):
        config = json.loads(self.config_file.read_text(encoding="utf-8"))
        config["platform_type"] = "wechat_local"
        self.config_file.write_text(json.dumps(config), encoding="utf-8")
        client = TgoClient(self.config_file, self.root, "account-a")
        client.connect()
        client.heartbeat()
        self.assertEqual(self.calls[-1], (
            "/v1/platforms/test-platform/wechat-local/heartbeat", "Bearer fake-im", {"channel_id": "visitor-vtr"},
        ))
        self.assertEqual(self.heartbeat_headers, ["test-channel-key"])

    def test_first_class_platform_renews_an_expired_cached_grant(self):
        config = json.loads(self.config_file.read_text(encoding="utf-8"))
        config["platform_type"] = "wechat_local"
        self.config_file.write_text(json.dumps(config), encoding="utf-8")
        first = TgoClient(self.config_file, self.root, "account-a")
        first.connect()
        restarted = TgoClient(self.config_file, self.root, "account-a")
        restarted.connect()
        registrations = [body for path, _, body in self.calls if path.endswith("/register")]
        self.assertEqual(len(registrations), 2)
        self.assertEqual(registrations[0]["platform_open_id"], registrations[1]["platform_open_id"])
        self.assertEqual(first.channel_id, restarted.channel_id)

    def test_only_first_class_platform_can_bind_https(self):
        self.assertEqual(
            bridge_base_url("https://support.example.test/api", "wechat_local"),
            "https://support.example.test/api",
        )
        for url, channel in (
            ("https://support.example.test/api", "website"),
            ("http://support.example.test/api", "wechat_local"),
            ("https://user@support.example.test/api", "wechat_local"),
        ):
            with self.subTest(url=url, channel=channel), self.assertRaises(ValueError):
                bridge_base_url(url, channel)
