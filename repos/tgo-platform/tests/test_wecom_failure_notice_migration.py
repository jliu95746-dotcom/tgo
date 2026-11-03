"""Historical media failures must never trigger a late automatic reply."""

from __future__ import annotations

import importlib
import sqlite3

import pytest


def test_historical_customer_media_failures_require_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.backfill_wecom_failure_notice_review"
    )
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE pt_wecom_inbox ("
        "id INTEGER PRIMARY KEY, source_type TEXT, is_from_colleague BOOLEAN, "
        "status TEXT, msg_type TEXT, failure_notice_status TEXT, "
        "failure_notice_error TEXT)"
    )
    records = [
        (1, "wecom_kf", False, "media_failed", "voice", None),
        (2, "wecom_kf", False, "unsupported_media", "file", None),
        (3, "wecom_kf", False, "dead", "image", None),
        (4, "wecom_kf", False, "dead", "text", None),
        (5, "wecom_kf", True, "media_failed", "voice", None),
        (6, "wecom", False, "media_failed", "voice", None),
        (7, "wecom_kf", False, "media_failed", "voice", "submitted"),
    ]
    db.executemany(
        "INSERT INTO pt_wecom_inbox "
        "(id, source_type, is_from_colleague, status, msg_type, failure_notice_status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        records,
    )
    monkeypatch.setattr(migration.op, "execute", db.execute)

    migration.upgrade()

    actual = dict(db.execute("SELECT id, failure_notice_status FROM pt_wecom_inbox"))
    assert actual == {
        1: "needs_attention",
        2: "needs_attention",
        3: "needs_attention",
        4: None,
        5: None,
        6: None,
        7: "submitted",
    }
