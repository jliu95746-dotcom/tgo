"""Verify the running staff-send API against a local callback, never a customer."""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


async def verify(base_url: str) -> None:
    if urlsplit(base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Only a local native platform service is allowed")
    received: list[dict[str, object]] = []
    callback_path = f"/delivery/{uuid4().hex}"

    class Callback(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            if self.path != callback_path:
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 65536:
                self.send_error(413)
                return
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "11")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *_: object) -> None:
            pass

    callback = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
    worker = threading.Thread(target=callback.serve_forever, daemon=True)
    worker.start()
    platform_id = str(uuid4())
    api_key = uuid4().hex
    created = False
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=15) as client:
            response = await client.post(
                "/v1/platforms",
                json={
                    "id": platform_id,
                    "project_id": str(uuid4()),
                    "name": "staff-send-local-e2e",
                    "type": "custom",
                    "is_active": True,
                    "api_key": api_key,
                    "config": {
                        "callback_url": f"http://127.0.0.1:{callback.server_port}{callback_path}"
                    },
                },
            )
            response.raise_for_status()
            created = True
            try:
                common = {
                    "platform_api_key": api_key,
                    "from_uid": "isolated-test-staff",
                    "channel_id": f"{uuid4()}-vtr",
                    "channel_type": 251,
                }
                bad = await client.post(
                    "/v1/messages/send",
                    json={
                        **common,
                        "payload": {"type": "invalid"},
                        "client_msg_no": "invalid-type",
                    },
                )
                assert bad.status_code == 400
                assert bad.json()["error"]["code"] == "INVALID_PAYLOAD"
                assert received == []
                print("PASS running API rejects invalid payload without delivery")
                for payload in [
                    {"type": 1, "content": "local smoke test"},
                    {
                        "type": 2,
                        "url": "https://example.invalid/test.png",
                        "width": 1,
                        "height": 1,
                    },
                    {
                        "type": 3,
                        "url": "https://example.invalid/test.txt",
                        "name": "test.txt",
                        "size": 1,
                    },
                    {
                        "type": 12,
                        "content": "local smoke test",
                        "images": [{"url": "https://example.invalid/test.png"}],
                    },
                ]:
                    # Custom channel contract carries the external identity in the
                    # payload; no real visitor or staff session is created.
                    payload["platform_open_id"] = "isolated-test-recipient"
                    message_no = uuid4().hex
                    response = await client.post(
                        "/v1/messages/send",
                        json={
                            **common,
                            "payload": payload,
                            "client_msg_no": message_no,
                        },
                    )
                    if response.is_error:
                        print(
                            f"Delivery failed: HTTP {response.status_code}, response={response.text}"
                        )
                    response.raise_for_status()
                    assert response.json()["ok"] is True
                    assert received[-1]["client_msg_no"] == message_no
                    assert received[-1]["payload"] == payload
                    print(
                        f"PASS real HTTP local callback delivery: payload type {payload['type']}"
                    )
                assert len(received) == 4
            finally:
                delivered_before_cleanup = len(received)
                deleted = await client.delete(f"/v1/platforms/{platform_id}")
                deleted.raise_for_status()
                assert deleted.status_code == 204
                check = await client.post(
                    "/v1/messages/send",
                    json={
                        "platform_api_key": api_key,
                        "from_uid": "isolated-test-staff",
                        "channel_id": f"{uuid4()}-vtr",
                        "channel_type": 251,
                        "payload": {"type": 1, "content": "must not deliver"},
                    },
                )
                assert check.status_code == 404
                assert len(received) == delivered_before_cleanup
                print(
                    "PASS cleanup: own test channel disabled/soft-deleted; deleted channel cannot deliver"
                )
    finally:
        callback.shutdown()
        callback.server_close()
        worker.join(timeout=5)
        if not created:
            print("No test channel was confirmed created")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.base_url))
