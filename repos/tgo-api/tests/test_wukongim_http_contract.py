"""Exercise the configured IM client over owned loopback HTTP sockets."""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.parse import urlsplit

import pytest
import httpx
from fastapi import HTTPException

from app.services.wukongim_client import WuKongIMClient
from tests.test_wukongim_transport_boundary import PRIVATE, connection


@pytest.fixture(scope="module")
def im_server():
    message = {
        "header": {"no_persist": 0, "red_dot": 0, "sync_once": 0},
        "setting": 0,
        "message_id": 7,
        "message_seq": 12,
        "client_msg_no": "fixture-message",
        "from_uid": "visitor-vtr",
        "channel_id": "fixture",
        "channel_type": 251,
        "timestamp": 1,
        "payload": base64.b64encode(
            b'{"type":1,"content":"fixture"}'
        ).decode(),
        "stream_data": [{"legacy": True}],
    }
    conversation = {
        "channel_id": "fixture",
        "channel_type": 251,
        "unread": 0,
        "timestamp": 1,
        "last_msg_seq": 12,
        "last_client_msg_no": "fixture-message",
        "version": 1,
        "recents": [message],
    }
    receipts = {
        "/message/send": {
            "data": {"message_id": 7, "client_msg_no": "fixture-message"}
        },
        "/channel/last_message": message,
        "/channel/max_message_seq": {"message_seq": 12},
        "/message/byclientmsgno": message,
        "/plugins/wk.plugin.search/usersearch": {
            "messages": [message],
            "total": 1,
        },
        "/user/onlinestatus": [
            {"uid": "staff", "online": 1, "device_flag": 1}
        ],
        "/conversation/sync": [conversation],
        "/conversation/syncByChannels": [conversation],
        "/channel/messagesync": {
            "start_message_seq": 0,
            "end_message_seq": 0,
            "more": 0,
            "messages": [message],
        },
        "/route": {"tcp_addr": "fixture:5100", "ws_addr": "ws://fixture:5200"},
    }
    records = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def respond(self):
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length)) if length else None
            records.append(
                (self.command, path, data, self.headers.get("Content-Type"))
            )
            body = json.dumps(receipts.get(path, {})).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = respond
        do_POST = respond

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        daemon=True,
    )
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", records
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        assert not worker.is_alive()


CALLS = [
    (
        "send_stream_event",
        "/message/event",
        dict(
            channel_id="fixture",
            channel_type=251,
            client_msg_no="fixture-message",
            event_id="fixture-event",
            event_type="stream.delta",
            payload={"delta": "fixture"},
        ),
    ),
    (
        "send_message",
        "/message/send",
        dict(
            channel_id="fixture",
            channel_type=251,
            client_msg_no="fixture-message",
            payload={"type": 1, "content": "fixture"},
        ),
    ),
    (
        "get_channel_last_message",
        "/channel/last_message",
        dict(channel_id="fixture", channel_type=251),
    ),
    (
        "get_channel_max_message_seq",
        "/channel/max_message_seq",
        dict(channel_id="fixture", channel_type=251, login_uid="staff"),
    ),
    (
        "get_message_by_client_msg_no",
        "/message/byclientmsgno",
        dict(
            channel_id="fixture",
            channel_type=251,
            client_msg_no="fixture-message",
            raise_on_error=True,
        ),
    ),
    (
        "create_channel",
        "/channel",
        dict(channel_id="fixture", channel_type=251, subscribers=["staff"]),
    ),
    (
        "add_channel_subscribers",
        "/channel/subscriber_add",
        dict(channel_id="fixture", channel_type=251, subscribers=["staff"]),
    ),
    (
        "remove_channel_subscribers",
        "/channel/subscriber_remove",
        dict(channel_id="fixture", channel_type=251, subscribers=["staff"]),
    ),
    (
        "remove_all_channel_subscribers",
        "/channel/subscriber_remove_all",
        dict(channel_id="fixture", channel_type=251),
    ),
    (
        "search_user_messages",
        "/plugins/wk.plugin.search/usersearch",
        dict(uid="staff", keyword="fixture"),
    ),
    (
        "register_or_login_user",
        "/user/token",
        dict(uid="staff", token="synthetic-fixture-token"),
    ),
    ("check_user_online_status", "/user/onlinestatus", dict(uids=["staff"])),
    ("add_system_accounts", "/user/systemuids_add", dict(uids=["staff"])),
    (
        "remove_system_accounts",
        "/user/systemuids_remove",
        dict(uids=["staff"]),
    ),
    ("kick_user_device", "/user/device_quit", dict(uid="staff")),
    ("sync_conversations", "/conversation/sync", dict(uid="staff")),
    (
        "sync_conversations_by_channels",
        "/conversation/syncByChannels",
        dict(
            uid="staff",
            channels=[{"channel_id": "fixture", "channel_type": 251}],
        ),
    ),
    (
        "set_conversation_unread",
        "/conversations/setUnread",
        dict(uid="staff", channel_id="fixture", channel_type=251, unread=0),
    ),
    (
        "delete_conversation",
        "/conversations/delete",
        dict(uid="staff", channel_id="fixture", channel_type=251),
    ),
    (
        "sync_channel_messages",
        "/channel/messagesync",
        dict(
            login_uid="staff",
            channel_id="fixture",
            channel_type=251,
            include_event_meta=1,
            event_summary_mode="full",
        ),
    ),
    ("get_route", "/route", dict(uid="staff")),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,path,arguments", CALLS, ids=[item[0] for item in CALLS]
)
async def test_normal_requests_and_receipts_over_http(
    im_server, operation, path, arguments
):
    base_url, records = im_server
    start = len(records)
    client = WuKongIMClient()
    client.enabled, client.base_url = True, base_url
    try:
        result = await getattr(client, operation)(**arguments)
    finally:
        await client.aclose()
    assert len(records) == start + 1
    method, actual_path, body, content_type = records[-1]
    assert actual_path == path
    if method == "POST":
        assert content_type == "application/json"
    if operation == "check_user_online_status":
        assert body == ["staff"] and result == ["staff"]
    elif operation in {"sync_conversations", "sync_conversations_by_channels"}:
        assert body["include_event_meta"] == 1 and body["stream_v2"] == 1
        assert result[0].recents[0].payload == {
            "type": 1,
            "content": "fixture",
        }
    elif operation == "send_message":
        assert (
            result.message_id == 7
            and result.client_msg_no == "fixture-message"
        )
    elif operation == "send_stream_event":
        assert body["event_type"] == "stream.delta" and body["payload"] == {
            "delta": "fixture"
        }
    elif operation == "sync_channel_messages":
        assert (
            body["include_event_meta"] == 1
            and body["event_summary_mode"] == "full"
        )
        assert result.messages[0].payload == {"type": 1, "content": "fixture"}
    elif operation == "get_channel_max_message_seq":
        assert result == 12
    elif operation == "search_user_messages":
        assert (
            result.total == 1
            and result.messages[0].payload["content"] == "fixture"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,path,arguments", CALLS, ids=[item[0] for item in CALLS]
)
async def test_public_client_errors_do_not_reexpose_upstream_text(
    operation, path, arguments, caplog
):
    async with connection(
        httpx.Response(503, json={"msg": PRIVATE})
    ) as client:
        invoke = getattr(client, operation)
        if operation in {
            "get_channel_last_message",
            "get_channel_max_message_seq",
        }:
            assert await invoke(**arguments) is None
        elif operation == "check_user_online_status":
            assert await invoke(**arguments) == []
        else:
            with pytest.raises(HTTPException) as failure:
                await invoke(**arguments)
            assert failure.value.status_code == 500
            assert PRIVATE not in str(failure.value.detail)
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,arguments,body",
    [
        ("sync_conversations", {"uid": "staff"}, [{"recents": [None]}]),
        (
            "sync_conversations_by_channels",
            {
                "uid": "staff",
                "channels": [{"channel_id": "fixture", "channel_type": 251}],
            },
            [{"recents": [None]}],
        ),
        (
            "sync_channel_messages",
            {
                "login_uid": "staff",
                "channel_id": "fixture",
                "channel_type": 251,
            },
            {"messages": [None]},
        ),
        ("search_user_messages", {"uid": "staff"}, {"messages": "invalid"}),
    ],
)
async def test_malformed_nested_messages_fail_at_the_response_boundary(
    operation, arguments, body
):
    async with connection(httpx.Response(200, json=body)) as client:
        with pytest.raises(HTTPException) as failure:
            await getattr(client, operation)(**arguments)
    assert failure.value.status_code == 502
