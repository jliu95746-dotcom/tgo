"""Email listener diagnostics must not print IMAP credentials or input data."""

from __future__ import annotations

import imaplib
from uuid import uuid4

import pytest

from app.domain.services.listeners import email_listener


SECRET = "fixture-imap-secret-marker"


@pytest.mark.asyncio
async def test_imap_connection_failure_does_not_print_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class IMAP:
        def __init__(self, *_args):
            pass

        def login(self, *_args):
            raise imaplib.IMAP4.error(SECRET)

    listener = email_listener.EmailChannelListener.__new__(
        email_listener.EmailChannelListener
    )
    listener._imap_conns = {}
    cfg = email_listener.EmailPlatformConfig(
        imap_host="imap.example.test",
        imap_username="private-user@example.test",
        imap_password=SECRET,
    )
    platform = email_listener._PlatformEntry(uuid4(), uuid4(), None, cfg)
    monkeypatch.setattr(email_listener.imaplib, "IMAP4_SSL", IMAP)

    assert await listener._get_imap_conn(platform) is None
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    assert cfg.imap_username not in output.out + output.err


@pytest.mark.asyncio
async def test_invalid_config_does_not_print_secret_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Result:
        def all(self):
            return [(uuid4(), uuid4(), None, {"imap_password": SECRET})]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _statement):
            return Result()

    listener = email_listener.EmailChannelListener.__new__(
        email_listener.EmailChannelListener
    )
    listener._session_factory = lambda: Session()

    assert await listener._load_active_email_platforms() == []
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
