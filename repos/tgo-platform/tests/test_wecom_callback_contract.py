from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.v1 import callbacks


class FakeRequest:
    def __init__(self, body: str, query_params: dict[str, str]) -> None:
        self._body = body.encode("utf-8")
        self.query_params = query_params
        self.state = SimpleNamespace(request_id="test-request-id")

    async def body(self) -> bytes:
        return self._body


class FakeSession:
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.added: list[object] = []
        self.commit_error = commit_error
        self.commits = 0
        self.rollbacks = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1
        if self.commit_error:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollbacks += 1


def make_platform() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        config={
            "token": "callback-token",
            "corp_id": "corp-id",
            "app_secret": "app-secret",
        },
    )


def make_signed_request(xml: str, *, timestamp: str = "1710000000", nonce: str = "nonce") -> FakeRequest:
    signature = callbacks.compute_msg_signature("callback-token", timestamp, nonce)
    return FakeRequest(
        xml,
        {
            "msg_signature": signature,
            "timestamp": timestamp,
            "nonce": nonce,
        },
    )


def make_integrity_error(sqlstate: str, constraint_name: str) -> IntegrityError:
    original = RuntimeError(f'constraint "{constraint_name}"')
    original.sqlstate = sqlstate
    original.diag = SimpleNamespace(constraint_name=constraint_name)
    return IntegrityError("INSERT", {}, original)


@pytest.mark.asyncio
async def test_kf_event_is_durably_queued_without_waiting_for_remote_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_sync(**_: object) -> None:
        await asyncio.sleep(1)

    monkeypatch.setattr(callbacks, "sync_kf_messages", slow_sync, raising=False)
    xml = """<xml>
        <ToUserName>corp-id</ToUserName>
        <CreateTime>1710000000</CreateTime>
        <MsgType>event</MsgType>
        <Event>kf_msg_or_event</Event>
        <Token>event-token</Token>
        <OpenKfId>wk-test</OpenKfId>
    </xml>"""
    session = FakeSession()

    response = await asyncio.wait_for(
        callbacks._handle_wecom_webhook(make_platform(), make_signed_request(xml), session),
        timeout=0.1,
    )

    assert response == {"ok": True}
    assert session.commits == 1
    assert len(session.added) == 1
    job = session.added[0]
    assert job.event_token == "event-token"
    assert job.open_kfid == "wk-test"
    assert len(job.callback_fingerprint) == 64
    assert job.status == "pending"


@pytest.mark.asyncio
async def test_distinct_kf_callbacks_do_not_share_a_deduplication_key() -> None:
    first_xml = """<xml>
        <CreateTime>1710000000</CreateTime>
        <MsgType>event</MsgType>
        <Event>kf_msg_or_event</Event>
        <Token>event-token</Token>
        <OpenKfId>wk-test</OpenKfId>
        <Sequence>1</Sequence>
    </xml>"""
    second_xml = first_xml.replace("<Sequence>1</Sequence>", "<Sequence>2</Sequence>")
    platform = make_platform()
    first_session = FakeSession()
    second_session = FakeSession()

    assert await callbacks._handle_wecom_webhook(
        platform,
        make_signed_request(first_xml),
        first_session,
    ) == {"ok": True}
    assert await callbacks._handle_wecom_webhook(
        platform,
        make_signed_request(second_xml),
        second_session,
    ) == {"ok": True}

    assert (
        first_session.added[0].callback_fingerprint
        != second_session.added[0].callback_fingerprint
    )


@pytest.mark.asyncio
async def test_non_kf_event_does_not_enqueue_sync_job(monkeypatch: pytest.MonkeyPatch) -> None:
    sync_calls = 0

    async def record_sync(**_: object) -> None:
        nonlocal sync_calls
        sync_calls += 1

    monkeypatch.setattr(callbacks, "sync_kf_messages", record_sync, raising=False)
    xml = """<xml>
        <CreateTime>1710000000</CreateTime>
        <MsgType>event</MsgType>
        <Event>change_contact</Event>
        <Token>event-token</Token>
        <OpenKfId>wk-test</OpenKfId>
    </xml>"""
    session = FakeSession()

    response = await callbacks._handle_wecom_webhook(
        make_platform(),
        make_signed_request(xml),
        session,
    )

    assert response == {"ok": True}
    assert session.added == []
    assert sync_calls == 0


@pytest.mark.asyncio
async def test_duplicate_kf_callback_is_acknowledged() -> None:
    xml = """<xml>
        <CreateTime>1710000000</CreateTime>
        <MsgType>event</MsgType>
        <Event>kf_msg_or_event</Event>
        <Token>event-token</Token>
        <OpenKfId>wk-test</OpenKfId>
    </xml>"""
    session = FakeSession(
        commit_error=make_integrity_error(
            "23505",
            "uq_wecom_sync_job_callback",
        )
    )

    response = await callbacks._handle_wecom_webhook(
        make_platform(),
        make_signed_request(xml),
        session,
    )

    assert response == {"ok": True}
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_non_duplicate_integrity_error_is_not_acknowledged() -> None:
    xml = """<xml>
        <CreateTime>1710000000</CreateTime>
        <MsgType>event</MsgType>
        <Event>kf_msg_or_event</Event>
        <Token>event-token</Token>
        <OpenKfId>wk-test</OpenKfId>
    </xml>"""
    session = FakeSession(
        commit_error=make_integrity_error(
            "23503",
            "pt_wecom_sync_jobs_platform_id_fkey",
        )
    )

    response = await callbacks._handle_wecom_webhook(
        make_platform(),
        make_signed_request(xml),
        session,
    )

    assert response.status_code == 500
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_callback_rejects_missing_timestamp_or_nonce() -> None:
    xml = "<xml><MsgType>text</MsgType><MsgId>message-1</MsgId></xml>"
    request = make_signed_request(xml, timestamp="", nonce="")

    response = await callbacks._handle_wecom_webhook(make_platform(), request, FakeSession())

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_business_message_rejects_missing_message_id() -> None:
    xml = """<xml>
        <CreateTime>1710000000</CreateTime>
        <MsgType>text</MsgType>
        <FromUserName>external-user</FromUserName>
        <Content>hello</Content>
    </xml>"""
    session = FakeSession()

    response = await callbacks._handle_wecom_webhook(
        make_platform(),
        make_signed_request(xml),
        session,
    )

    assert response.status_code == 400
    assert session.added == []
