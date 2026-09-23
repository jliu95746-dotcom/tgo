"""Two companies exercise real HTTP authorization and real IM persistence.

Only UUID-named synthetic channels are created or removed. Their fixed text
may remain in IM storage after channel deletion; this is not a storage purge.
No customer, model, WeCom or external channel is contacted.
"""

import asyncio
import logging
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import uvicorn
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "repos/tgo-api"))

from app.api.v1.endpoints.conversations import router  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import get_db, sync_engine  # noqa: E402
from app.core.exceptions import (  # noqa: E402
    TGOAPIException,
    tgo_api_exception_handler,
)
from app.core.security import create_access_token  # noqa: E402
from app.models import (  # noqa: E402
    ChannelMemoryClearance,
    Platform,
    Project,
    Staff,
    Visitor,
)
from app.services.wukongim_client import wukongim_client as im  # noqa: E402


@dataclass(frozen=True)
class Actor:
    uid: str
    token: str
    company: int


@dataclass(frozen=True)
class TestChannel:
    visitor_id: UUID
    channel_id: str
    content: str


def seed(factory):
    actors, channels = [], []
    with factory() as db:
        for index in range(2):
            company = Project(
                name="im-isolation-" + uuid4().hex,
                api_key=token_urlsafe(24),
            )
            db.add(company)
            db.flush()
            platform = Platform(
                project_id=company.id,
                name="synthetic",
                type="website",
                api_key=token_urlsafe(24),
                is_active=True,
            )
            db.add(platform)
            db.flush()
            visitor = Visitor(
                project_id=company.id,
                platform_id=platform.id,
                platform_open_id=uuid4().hex,
            )
            db.add(visitor)
            db.flush()
            channels.append(
                TestChannel(
                    visitor.id,
                    f"{visitor.id}-vtr",
                    f"isolated-company-{index}",
                )
            )
            for role in ("admin", "user"):
                staff = Staff(
                    project_id=company.id,
                    username="fixture-" + uuid4().hex,
                    role=role,
                    password_hash="cannot-login-fixture",
                )
                db.add(staff)
                db.flush()
                actors.append(
                    Actor(
                        f"{staff.id}-staff",
                        create_access_token(staff.username, company.id, role),
                        index,
                    )
                )
        db.commit()
    return actors, channels


async def im_conversation(actor, channel):
    records = await im.sync_conversations(uid=actor.uid)
    return next(
        (
            row
            for row in records
            if row.channel_id == channel.channel_id and row.channel_type == 251
        ),
        None,
    )


async def wait_conversation(actor, channel, unread):
    deadline = asyncio.get_running_loop().time() + 10
    while True:
        row = await im_conversation(actor, channel)
        if (unread is None and row is None) or (
            row is not None and row.unread == unread
        ):
            return row
        if asyncio.get_running_loop().time() >= deadline:
            actual = None if row is None else row.unread
            raise AssertionError(f"IM unread expected {unread}, got {actual}")
        await asyncio.sleep(0.1)


async def request(client, actor, channel, action, forged_uid):
    body = {
        "uid": forged_uid,
        "channel_id": channel.channel_id,
        "channel_type": 251,
    }
    method, path = {
        "history": ("POST", "/v1/conversations/messages"),
        "unread": ("PUT", "/v1/conversations/unread"),
        "delete": ("DELETE", "/v1/conversations"),
    }[action]
    if action == "unread":
        body["unread"] = 1
    return await client.request(
        method,
        path,
        json=body,
        headers={"Authorization": "Bearer " + actor.token},
    )


async def verify(client, actors, channels):
    baseline = {}
    for actor in actors:
        channel = channels[actor.company]
        row = await wait_conversation(actor, channel, 3)
        baseline[actor.uid] = row.model_dump()
    sys.stdout.write(
        "PASS: four synthetic staff conversations hold three IM messages.\n"
    )

    for actor in actors:
        foreign = channels[1 - actor.company]
        forged_uid = actors[2 * (1 - actor.company)].uid
        for action in ("history", "unread", "delete"):
            result = await request(client, actor, foreign, action, forged_uid)
            assert result.status_code == 404, (action, result.status_code)
    for actor in actors:
        row = await im_conversation(actor, channels[actor.company])
        assert row.model_dump() == baseline[actor.uid]
    sys.stdout.write(
        "PASS: both roles rejected for foreign history, unread and deletion; "
        "IM state unchanged.\n"
    )

    for actor in actors:
        own = channels[actor.company]
        forged_uid = actors[2 * (1 - actor.company)].uid
        history = await request(client, actor, own, "history", forged_uid)
        assert history.status_code == 200, history.status_code
        messages = history.json()["messages"]
        assert len(messages) == 3
        assert {m["payload"]["content"] for m in messages} == {own.content}
        assert all(m["channel_id"] == own.channel_id for m in messages)
        others_before = {
            other.uid: (await im_conversation(
                other, channels[other.company],
            )).model_dump()
            for other in actors if other != actor
        }
        result = await request(client, actor, own, "unread", forged_uid)
        assert result.status_code == 200, result.status_code
        await wait_conversation(actor, own, 1)
        for other in actors:
            if other != actor:
                row = await im_conversation(other, channels[other.company])
                assert row.model_dump() == others_before[other.uid]
    sys.stdout.write(
        "PASS: own history returns exact persisted payloads; "
        "unread changes target authenticated staff.\n"
    )

    for index, actor in enumerate(actors):
        own = channels[actor.company]
        forged_uid = actors[2 * (1 - actor.company)].uid
        other_actors = actors[index + 1:]
        remaining = {
            other.uid: (
                await im_conversation(
                    other,
                    channels[other.company],
                )
            ).model_dump()
            for other in other_actors
        }
        result = await request(client, actor, own, "delete", forged_uid)
        assert result.status_code == 200, result.status_code
        await wait_conversation(actor, own, None)
        for other in other_actors:
            row = await im_conversation(other, channels[other.company])
            assert row.model_dump() == remaining[other.uid]
        history = await request(client, actor, own, "history", forged_uid)
        assert history.status_code == 200
        assert len(history.json()["messages"]) == 3
    sys.stdout.write(
        "PASS: deletion affects only authenticated staff's conversation; "
        "others and persisted history remain intact.\n"
    )


async def main():
    assert settings.WUKONGIM_ENABLED, "IM must be enabled"
    assert urlsplit(im.base_url).hostname in {"127.0.0.1", "localhost", "::1"}
    logging.disable(logging.CRITICAL)
    marker = "conversation_im_" + uuid4().hex
    server = task = listener = None
    channels_created, actors, channels = [], [], []
    with sync_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f'CREATE SCHEMA "{marker}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{marker}"'))
            for model in (
                Project,
                Staff,
                Platform,
                Visitor,
                ChannelMemoryClearance,
            ):
                model.__table__.create(connection)
            factory = sessionmaker(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            )

            def private_db():
                with factory() as db:
                    yield db

            with patch.object(settings, "SECRET_KEY", token_urlsafe(48)):
                actors, channels = seed(factory)
                async with httpx.AsyncClient(
                    base_url=im.base_url,
                    timeout=10,
                    trust_env=False,
                ) as control:
                    for index, channel in enumerate(channels):
                        channels_created.append(channel)
                        response = await control.post(
                            "/channel",
                            json={
                                "channel_id": channel.channel_id,
                                "channel_type": 251,
                                "subscribers": [
                                    actor.uid
                                    for actor in actors
                                    if actor.company == index
                                ]
                                + [channel.channel_id],
                            },
                        )
                        assert response.status_code == 200
                        for _ in range(3):
                            sent = await im.send_message(
                                from_uid=channel.channel_id,
                                channel_id=channel.channel_id,
                                channel_type=251,
                                no_persist=False,
                                payload={
                                    "type": 1,
                                    "content": channel.content,
                                },
                            )
                            assert sent is not None and sent.message_id > 0
                app = FastAPI()
                app.add_exception_handler(
                    TGOAPIException,
                    tgo_api_exception_handler,
                )
                app.include_router(router, prefix="/v1/conversations")
                app.dependency_overrides[get_db] = private_db
                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                listener.listen(16)
                listener.setblocking(False)
                port = listener.getsockname()[1]
                server = uvicorn.Server(
                    uvicorn.Config(
                        app,
                        lifespan="off",
                        access_log=False,
                        log_level="critical",
                    )
                )
                task = asyncio.create_task(server.serve(sockets=[listener]))
                for _ in range(100):
                    if server.started:
                        break
                    if task.done():
                        await task
                        raise AssertionError("HTTP fixture stopped")
                    await asyncio.sleep(0.05)
                assert server.started
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}",
                    timeout=15,
                    trust_env=False,
                ) as client:
                    await verify(client, actors, channels)
        finally:
            cleanup_errors = []
            try:
                if server is not None:
                    server.should_exit = True
                if task is not None:
                    await asyncio.wait_for(task, 15)
            finally:
                if listener is not None:
                    listener.close()
                # Only UUID channels and identities allocated by this run.
                async with httpx.AsyncClient(
                    base_url=im.base_url,
                    timeout=10,
                    trust_env=False,
                ) as control:
                    for channel in channels_created:
                        owners = [
                            actor.uid
                            for actor in actors
                            if channels[actor.company] == channel
                        ] + [channel.channel_id]
                        for uid in owners:
                            try:
                                await im.delete_conversation(
                                    uid=uid,
                                    channel_id=channel.channel_id,
                                    channel_type=251,
                                )
                            except Exception:
                                cleanup_errors.append("synthetic conversation")
                        try:
                            response = await control.post(
                                "/channel/delete",
                                json={
                                    "channel_id": channel.channel_id,
                                    "channel_type": 251,
                                },
                            )
                            response.raise_for_status()
                        except Exception:
                            cleanup_errors.append("synthetic channel")
                transaction.rollback()
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_namespace "
                            "WHERE nspname=:name"
                        ),
                        {"name": marker},
                    )
                    == 0
                )
                await im.aclose()
            assert not cleanup_errors, cleanup_errors
            sys.stdout.write(
                "PASS: HTTP stopped, test conversations/channels removed; "
                "SQL rolled back; IM message storage not purged.\n"
            )


if __name__ == "__main__":
    asyncio.run(main())
