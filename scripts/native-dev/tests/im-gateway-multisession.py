"""Verify concurrent sessions against local IM with disposable DB identities.

Synthetic channel messages are not persisted. Cleanup revokes only identities
and channels created here and rolls back the private database schema.
"""
import asyncio
import base64
import json
import socket
import sys
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from websockets.legacy.client import connect
from websockets.exceptions import ConnectionClosed

# This standalone test loads the API package from its workspace service root.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "repos/tgo-api"))
from app.api.v1.endpoints.wukongim import router  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import sync_engine  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.models import Project, Staff, Platform, Visitor  # noqa: E402
from app.services import im_gateway  # noqa: E402
from app.services.im_access import issue_visitor_im_token  # noqa: E402


async def receive(ws):
    return json.loads(await asyncio.wait_for(ws.recv(), 8))


async def rpc(ws, method, params):
    request_id = uuid4().hex
    await ws.send(
        json.dumps({"id": request_id, "method": method, "params": params})
    )
    while True:
        result = await receive(ws)
        if result.get("method") == "disconnect":
            raise AssertionError("IM evicted an existing parallel session")
        if method == "ping" and result.get("method") == "pong":
            return result
        if result.get("id") == request_id:
            return result
        if result.get("method") == "recv":
            p = result["params"]
            await ws.send(
                json.dumps(
                    {
                        "method": "recvack",
                        "params": {
                            "messageId": p["messageId"],
                            "messageSeq": p["messageSeq"],
                            "header": p.get("header", {}),
                        },
                    }
                )
            )


async def main():
    for configured_url in (
        settings.WUKONGIM_SERVICE_URL,
        settings.SAAS_IM_UPSTREAM_URL,
    ):
        if urlsplit(configured_url).hostname not in (
            "127.0.0.1",
            "localhost",
            "::1",
        ):
            raise ValueError("This probe only accepts local IM services")
    schema = "im_gateway_verify_" + uuid4().hex
    identities, channels = [], []
    server = server_task = None
    connection = sync_engine.connect()
    transaction = connection.begin()
    async with httpx.AsyncClient(
        base_url=settings.WUKONGIM_SERVICE_URL, timeout=8
    ) as http:

        async def api(path, body):
            response = await http.post(path, json=body)
            if response.status_code != 200:
                raise RuntimeError(
                    f"IM {path} returned HTTP {response.status_code}"
                )

        try:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            for model in (Project, Staff, Platform, Visitor):
                model.__table__.create(connection)
            factory = sessionmaker(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            )
            with factory() as db:
                companies = [
                    Project(name=schema + name, api_key=uuid4().hex)
                    for name in ("a", "b")
                ]
                db.add_all(companies)
                db.flush()
                staff = [
                    Staff(
                        project_id=c.id,
                        username="probe_" + uuid4().hex,
                        role="admin",
                        password_hash="unused",
                    )
                    for c in companies
                ]
                platforms = [
                    Platform(
                        project_id=c.id,
                        name=c.name,
                        type="website",
                        api_key=uuid4().hex,
                        is_active=True,
                    )
                    for c in companies
                ]
                db.add_all(staff + platforms)
                db.flush()
                visitors = [
                    Visitor(
                        project_id=c.id,
                        platform_id=p.id,
                        platform_open_id=uuid4().hex,
                    )
                    for c, p in zip(companies, platforms)
                ]
                db.add_all(visitors)
                db.commit()
                for actor in staff:
                    identities.append(
                        {
                            "uid": f"{actor.id}-staff",
                            "token": create_access_token(
                                actor.username, actor.project_id, actor.role
                            ),
                        }
                    )
                for actor in visitors:
                    identities.append(
                        {
                            "uid": f"{actor.id}-vtr",
                            "token": issue_visitor_im_token(
                                actor, timedelta(minutes=10)
                            ),
                        }
                    )
                for identity in identities:
                    await api(
                        "/user/token",
                        {
                            **identity,
                            "device_flag": settings.WUKONGIM_DEVICE_FLAG,
                            "device_level": 1,
                        },
                    )
                for i, visitor in enumerate(visitors):
                    channel = {
                        "channel_id": f"{visitor.id}-vtr",
                        "channel_type": 251,
                    }
                    channels.append(channel)
                    await api(
                        "/channel",
                        {
                            **channel,
                            "subscribers": [
                                identities[i]["uid"],
                                identities[i + 2]["uid"],
                            ],
                        },
                    )
                im_gateway.SessionLocal = factory
                im_gateway.REVALIDATE_SECONDS = 0.1
                settings.SAAS_ENABLED = True
                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                listener.listen(64)
                origin = f"ws://127.0.0.1:{listener.getsockname()[1]}"
                url = origin + "/v1/wukongim/ws"
                settings.SAAS_IM_PUBLIC_URL = url
                app = FastAPI()
                app.include_router(router, prefix="/v1/wukongim")
                server = uvicorn.Server(
                    uvicorn.Config(app, log_level="error", lifespan="off")
                )
                server_task = asyncio.create_task(
                    server.serve(sockets=[listener])
                )
                for _ in range(100):
                    if server.started:
                        break
                    await asyncio.sleep(0.05)
                assert server.started, "Temporary gateway did not start"
                async with AsyncExitStack() as stack:
                    clients = []
                    for identity in identities:
                        ws = await stack.enter_async_context(
                            connect(url, close_timeout=1)
                        )
                        result = await rpc(
                            ws,
                            "connect",
                            {
                                **identity,
                                "deviceId": "____device",
                                "deviceFlag": 1,
                            },
                        )
                        assert "result" in result, "Synthetic login failed"
                        clients.append(ws)
                    print(
                        "PASS: four synthetic identities authenticated "
                        "through real IM",
                        flush=True,
                    )
                    for identity in (identities[0], identities[2]):
                        extra_ws = await stack.enter_async_context(
                            connect(url, close_timeout=1)
                        )
                        result = await rpc(
                            extra_ws,
                            "connect",
                            {
                                **identity,
                                "deviceId": "second-tab",
                                "deviceFlag": 1,
                            },
                        )
                        assert "result" in result, "Second tab login failed"
                        clients.append(extra_ws)
                    # Exercise idle heartbeats too: a successful handshake
                    # alone does not prove that parallel sessions stay alive.
                    for _ in range(6):
                        await asyncio.sleep(5)
                        for client in clients:
                            pong = await rpc(client, "ping", {})
                            assert "error" not in pong, "Heartbeat rejected"
                    print("PASS: all six sessions survive idle heartbeats",
                          flush=True)
                    notify_profile = (
                        im_gateway.wukongim_client.send_visitor_profile_updated
                    )
                    await notify_profile(
                        visitor_id=str(visitors[0].id),
                        channel_id=identities[2]["uid"],
                        channel_type=251,
                    )
                    profile_clients = (
                        clients[0], clients[4], clients[2], clients[5]
                    )
                    for client in profile_clients:
                        event = await receive(client)
                        assert event.get("method") == "recv"
                        body = json.loads(base64.b64decode(
                            event["params"]["payload"]
                        ))
                        assert body["cmd"] == "visitor.profile.updated"
                    print(
                        "PASS: all staff and visitor tabs receive "
                        "profile update",
                        flush=True,
                    )
                    payload = base64.b64encode(
                        b'{"type":1,"content":"synthetic gateway probe"}'
                    ).decode()

                    async def send(client, channel, kind):
                        return await rpc(
                            client,
                            "send",
                            {
                                "channelId": channel,
                                "channelType": kind,
                                "clientMsgNo": uuid4().hex,
                                "header": {"noPersist": True, "redDot": False},
                                "payload": payload,
                            },
                        )

                    for client, target, kind in [
                        (clients[0], identities[1]["uid"], 1),
                        (clients[1], identities[2]["uid"], 251),
                        (clients[2], identities[3]["uid"], 251),
                    ]:
                        assert (await send(client, target, kind))["error"][
                            "code"
                        ] == 403
                    print(
                        "PASS: staff and visitor cross-company sends rejected",
                        flush=True,
                    )
                    sent = await send(clients[0], identities[2]["uid"], 251)
                    assert (
                        sent["result"]["reasonCode"] == 1
                    ), "Own channel send failed"
                    delivered = await receive(clients[2])
                    assert delivered["method"] == "recv"
                    assert (
                        delivered["params"]["channelId"]
                        == identities[2]["uid"]
                    )
                    assert delivered["params"]["payload"] == payload
                    duplicate = await receive(clients[5])
                    assert duplicate["method"] == "recv"
                    assert duplicate["params"]["payload"] == payload
                    second_sent = await send(
                        clients[4], identities[2]["uid"], 251
                    )
                    assert second_sent["result"]["reasonCode"] == 1
                    for visitor_ws in (clients[2], clients[5]):
                        assert (await receive(visitor_ws))["params"][
                            "payload"
                        ] == payload
                    print(
                        "PASS: both staff tabs send and both visitor tabs "
                        "receive without eviction",
                        flush=True,
                    )
                    platforms[0].is_active = False
                    db.commit()
                    for visitor_ws in (clients[2], clients[5]):
                        try:
                            await receive(visitor_ws)
                            raise AssertionError(
                                "Revoked visitor socket remained open"
                            )
                        except ConnectionClosed as error:
                            assert error.code == 1008
                    print(
                        "PASS: both visitor tabs disconnected "
                        "after platform revocation",
                        flush=True,
                    )
        finally:
            if server:
                server.should_exit = True
            if server_task:
                await asyncio.wait_for(server_task, 10)
            for identity in identities:
                await api(
                    "/user/device_quit",
                    {"uid": identity["uid"], "device_flag": -1},
                )
            for channel in channels:
                await api("/channel/delete", channel)
            transaction.rollback()
            connection.close()
            with sync_engine.connect() as check:
                assert (
                    check.execute(
                        text(
                            "SELECT count(*) FROM pg_namespace "
                            "WHERE nspname=:schema"
                        ),
                        {"schema": schema},
                    ).scalar_one()
                    == 0
                )
            print(
                "PASS: synthetic credentials revoked, channels removed, "
                "DB schema rolled back",
                flush=True,
            )


asyncio.run(main())
