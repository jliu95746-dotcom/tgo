"""Opt-in real Lua/TTL checks; never touch a business queue or shared key."""

import os
from typing import cast
from uuid import uuid4

import pytest
from redis import Redis
from sqlalchemy.engine import make_url
from app.services.timer_probe import (
    ProbeStore, assess_runtime, consume_probe, issue_probe, probe_key,
)


@pytest.fixture
def probe_store():
    target = os.environ.get("TIMER_REDIS_TEST_URL")
    if not target:
        pytest.skip("Explicit TIMER_REDIS_TEST_URL is required")
    if make_url(target).host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("Timer probe integration accepts only loopback Redis")
    scope = "test-" + uuid4().hex
    with Redis.from_url(target, socket_connect_timeout=0.5, socket_timeout=0.5) as client:
        client.ping()
        try:
            yield client, scope, cast(ProbeStore, client)
        finally:
            keys = list(client.scan_iter(match=probe_key(scope, "*"), count=50))
            if keys:
                client.delete(*keys)
            assert not list(client.scan_iter(match=probe_key(scope, "*"), count=50))


def test_actual_lua_single_consumption_and_bounded_ttl(probe_store):
    client, scope, store = probe_store
    sent = []
    issue_probe(store, scope, uuid4(), sent.append)
    message = sent[0]
    challenge_key = probe_key(scope, "challenge:" + message.probe_id.hex)
    assert 0 < client.ttl(probe_key(scope, "beat")) <= 45
    assert 0 < client.ttl(challenge_key) <= 30
    assert consume_probe(store, scope, message, lambda: True)
    before = client.get(probe_key(scope, "worker"))
    assert client.exists(challenge_key) == 0
    assert 0 < client.ttl(probe_key(scope, "worker")) <= 45

    def forbidden():
        raise AssertionError("Duplicate delivery must not probe the database")

    assert not consume_probe(store, scope, message, forbidden)
    assert client.get(probe_key(scope, "worker")) == before
    state = assess_runtime(
        client.get(probe_key(scope, "beat")), before, now=client.time()[0],
    )
    assert state.ready
    client.expire(probe_key(scope, "beat"), 0)
    assert not assess_runtime(None, before, now=client.time()[0]).ready


def test_actual_lua_rejects_scheduler_replaced_during_database_check(probe_store):
    client, scope, store = probe_store
    sent = []
    issue_probe(store, scope, uuid4(), sent.append)

    def replace_scheduler():
        issue_probe(store, scope, uuid4(), sent.append)
        return True

    assert not consume_probe(store, scope, sent[0], replace_scheduler)
    assert client.get(probe_key(scope, "worker")) is None
    assert consume_probe(store, scope, sent[1], lambda: False)
    state = assess_runtime(
        client.get(probe_key(scope, "beat")),
        client.get(probe_key(scope, "worker")), now=client.time()[0],
    )
    assert state.status == "database_unavailable" and not state.ready
