"""A probe receipt belongs to one scheduler instance and is consumed once."""

from uuid import uuid4
from app.services.timer_probe import (
    ACKNOWLEDGE, ISSUE, consume_probe, decode_probe, issue_probe, probe_key,
)


class Store:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.now = 100

    def get(self, key):
        return self.values.get(key)

    def time(self):
        return self.now, 0

    def eval(self, script, count, *values):
        keys, args = values[:count], values[count:]
        if script == ISSUE:
            self.values.update({keys[0]: args[0], keys[1]: args[1]})
            return 1
        assert script == ACKNOWLEDGE
        if self.get(keys[0]) != args[0] or self.get(keys[1]) != args[1]:
            return 0
        self.values[keys[2]] = args[2]
        del self.values[keys[1]]
        return 1


def test_repeat_delivery_cannot_refresh_worker_evidence():
    store, sent = Store(), []
    issue_probe(store, "test", uuid4(), sent.append)
    checks = []
    assert consume_probe(store, "test", sent[0], lambda: checks.append(1) or True)
    before = store.get(probe_key("test", "worker"))
    store.now += 10
    assert not consume_probe(store, "test", sent[0], lambda: checks.append(2) or True)
    assert checks == [1]
    assert store.get(probe_key("test", "worker")) == before


def test_expired_or_foreign_probe_does_not_read_the_database():
    store, sent = Store(), []
    issue_probe(store, "original", uuid4(), sent.append)

    def forbidden():
        raise AssertionError("Must not connect for an untrusted challenge")

    assert not consume_probe(store, "foreign", sent[0], forbidden)
    store.now += 31
    assert not consume_probe(store, "original", sent[0], forbidden)


def test_scheduler_replacement_during_database_check_rejects_old_receipt():
    store, sent = Store(), []
    issue_probe(store, "test", uuid4(), sent.append)

    def replace_scheduler():
        issue_probe(store, "test", uuid4(), sent.append)
        return True

    assert not consume_probe(store, "test", sent[0], replace_scheduler)
    assert store.get(probe_key("test", "worker")) is None


def test_database_failure_is_persisted_without_exception_text():
    store, sent = Store(), []
    issue_probe(store, "test", uuid4(), sent.append)

    def unavailable():
        raise RuntimeError("synthetic-private-database-url")

    assert consume_probe(store, "test", sent[0], unavailable)
    raw = store.get(probe_key("test", "worker"))
    assert decode_probe(raw, "worker").database_ready is False
    assert "synthetic-private" not in raw
