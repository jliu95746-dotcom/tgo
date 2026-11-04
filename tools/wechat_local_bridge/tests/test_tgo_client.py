import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from tools.wechat_local_bridge.tgo_client import TgoClient, TgoError, bridge_base_url
from tools.wechat_local_bridge.media_types import MediaContent


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
                elif self.path.endswith("wechat-local-deliveries/ready"):
                    status, result = 200, {"replies": []}
                elif "/wechat-local-deliveries/" in self.path:
                    status, result = 200, ({"claim_id": "00000000-0000-0000-0000-000000000072"}
                        if self.path.endswith("claim") else {"accepted": True})
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

    def test_staff_delivery_uses_bound_cloud_channel_and_idempotent_attempt(self):
        self.client.connect()
        self.client.scoped = True
        self.assertEqual(self.client.staff_replies(), [])
        identity, attempt = str(uuid4()), str(uuid4())
        self.client.staff_action(identity, "claim", attempt_id=attempt)
        first = self.calls[-1]
        self.client.staff_action(identity, "claim", attempt_id=attempt)
        self.assertEqual(first, self.calls[-1])
        self.assertEqual(first, (
            f"/v1/chat/wechat-local-deliveries/{identity}/claim", "Bearer fake-im",
            {"platform_api_key": "test-channel-key", "channel_id": "visitor-vtr", "channel_type": 251,
             "attempt_id": attempt},
        ))
        self.client.staff_action(identity, "receipt", claim_id="00000000-0000-0000-0000-000000000072",
                                 result="sent", receipt="a" * 64)
        with self.assertRaises(ValueError):
            self.client.staff_action("../other-target", "claim", attempt_id=attempt)

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
        self.assertNotIn("visitor_access_token", registrations[0])
        self.assertEqual(registrations[1]["visitor_access_token"], first.session["visitor_access_token"])
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

    def test_legacy_cached_session_refresh_supplies_its_original_signed_proof(self):
        self.client.connect()
        cached = json.loads(self.client.session_file.read_text(encoding="utf-8"))
        cached.pop("visitor_access_token")
        self.client.session_file.write_text(json.dumps(cached), encoding="utf-8")
        restarted = TgoClient(self.config_file, self.root, "account-a")
        restarted.connect(force_refresh=True)
        registration = [body for path, _, body in self.calls if path.endswith("/register")][-1]
        self.assertEqual(registration["im_token"], cached["im_token"])
        self.assertNotIn("visitor_access_token", registration)
        self.assertEqual(restarted.channel_id, self.client.channel_id)

    def test_current_access_proof_does_not_send_a_second_legacy_proof(self):
        self.client.connect()
        self.client.connect(force_refresh=True)
        registration = [body for path, _, body in self.calls if path.endswith("/register")][-1]
        self.assertEqual(registration["visitor_access_token"], self.client.session["visitor_access_token"])
        self.assertNotIn("im_token", registration)

    def test_media_upload_uses_visitor_token_platform_key_and_owned_channel(self):
        config = json.loads(self.config_file.read_text())
        config.update(chat_id="scoped", platform_type="wechat_local")
        self.config_file.write_text(json.dumps(config))
        client = TgoClient(self.config_file, self.root, "account-a", scoped=True, identity="fixture-identity")
        client.session = {"channel_id": "visitor-vtr", "id": "visitor", "im_token": "fixture-token",
                          "platform_open_id": "fixture-identity"}
        file_id = str(uuid4())
        with patch("tools.wechat_local_bridge.tgo_client.request_json", return_value={
            "channel_id": "visitor-vtr", "channel_type": 251, "file_id": file_id,
        }) as upload:
            media = client.upload_media(MediaContent("voice", "audio/silk", b"fixture-silk"))
            request = upload.call_args.args[1]
            self.assertEqual(request.get_header("Authorization"), "Bearer fixture-token")
            self.assertEqual(request.get_header("X-platform-api-key"), "test-channel-key")
            self.assertIn(b'name="channel_id"\r\n\r\nvisitor-vtr', request.data)
            self.assertIn(b"Content-Type: audio/silk", request.data)
            self.assertEqual(media.file_id, file_id)
        client.submit("a" * 64, "", media=media)
        body = self.calls[-1][2]
        self.assertEqual(body["msg_type"], 4)
        self.assertEqual(body["media_file_id"], file_id)
        self.assertEqual(body["source_message_id"], "wxlocal:"+"a"*64)
        with patch("tools.wechat_local_bridge.tgo_client.request_json", return_value={
            "channel_id": "other-vtr", "channel_type": 251, "file_id": file_id,
        }):
            with self.assertRaises(TgoError):
                client.upload_media(MediaContent("image", "image/png", b"fixture"))
