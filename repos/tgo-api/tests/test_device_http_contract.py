"""Signed device requests use owned loopback sockets and synthetic replies."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from jose import jwt
import pytest

from app.core.config import settings
from app.services.device_control_client import DeviceControlClient


@pytest.fixture(scope="module")
def device_server():
    state = SimpleNamespace(
        project=str(uuid4()), device=str(uuid4()), records=[]
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def respond(self):
            parsed = urlsplit(self.path)
            token = self.headers.get("Authorization", "")
            claims = jwt.decode(
                token.removeprefix("Bearer "),
                settings.SECRET_KEY,
                algorithms=["HS256"],
                audience="tgo-device-control",
                issuer="tgo-internal",
            )
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else None
            state.records.append(
                (
                    self.command,
                    parsed.path,
                    parse_qs(parsed.query),
                    body,
                    claims,
                )
            )
            response = json.dumps(
                {
                    "devices": [],
                    "total": 0,
                    "tools": [],
                    "success": True,
                    "device_id": state.device,
                    "project_id": state.project,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        do_GET = respond
        do_POST = respond
        do_PATCH = respond
        do_DELETE = respond

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        daemon=True,
    )
    worker.start()
    state.base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        assert not worker.is_alive()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "list",
        "get",
        "bind",
        "patch",
        "delete",
        "disconnect",
        "tools",
        "connected",
    ],
)
async def test_signed_normal_device_operations_over_http(
    device_server, operation
):
    state = device_server
    client = DeviceControlClient()
    client.base_url = state.base_url
    before = len(state.records)
    if operation == "list":
        result = await client.list_devices(
            state.project,
            device_type="desktop",
            status="online",
            skip=2,
            limit=10,
        )
        expected = "GET", "/v1/devices"
    elif operation == "get":
        result = await client.get_device(state.device, state.project)
        expected = "GET", f"/v1/devices/{state.device}"
    elif operation == "bind":
        result = await client.generate_bind_code(state.project)
        expected = "POST", "/v1/devices/bind-code"
    elif operation == "patch":
        result = await client.update_device(
            state.device, state.project, {"device_name": "Fixture"}
        )
        expected = "PATCH", f"/v1/devices/{state.device}"
    elif operation == "delete":
        result = await client.delete_device(state.device, state.project)
        expected = "DELETE", f"/v1/devices/{state.device}"
    elif operation == "disconnect":
        result = await client.disconnect_device(state.device, state.project)
        expected = "POST", f"/v1/devices/{state.device}/disconnect"
    elif operation == "tools":
        result = await client.get_device_tools(state.device, state.project)
        expected = "GET", f"/v1/mcp/tools/{state.device}"
    else:
        result = await client.list_connected_devices(state.project)
        expected = "GET", "/v1/devices/connected"
    assert len(state.records) == before + 1
    method, path, query, body, claims = state.records[-1]
    assert (method, path) == expected
    assert claims["project_id"] == state.project and claims["sub"] == "tgo-api"
    assert 0 < claims["exp"] - claims["iat"] <= 300
    if operation == "list":
        assert query == {
            "project_id": [state.project],
            "device_type": ["desktop"],
            "status": ["online"],
            "skip": ["2"],
            "limit": ["10"],
        }
    if operation == "patch":
        assert body == {"device_name": "Fixture"}
    if operation in {"delete", "disconnect"}:
        assert result is True
    else:
        assert result["project_id"] == state.project
