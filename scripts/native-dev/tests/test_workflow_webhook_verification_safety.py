"""Reject remote infrastructure before creating a database or child process."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.engine import make_url


def load_verifier():
    path = Path(__file__).with_name("workflow-webhooks-live.py")
    spec = importlib.util.spec_from_file_location(
        "webhook_live_verifier", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("database_url", "redis_url"),
    [
        (
            "postgresql+asyncpg://test@198.51.100.7/disposable",
            "redis://127.0.0.1/3",
        ),
        (
            "postgresql+asyncpg://test@database.example.invalid/disposable",
            "redis://127.0.0.1/3",
        ),
        (
            "postgresql+asyncpg://test@127.0.0.1/disposable",
            "redis://198.51.100.8/3",
        ),
    ],
)
async def test_remote_targets_create_nothing(
    monkeypatch, tmp_path, database_url, redis_url
):
    verifier = load_verifier()
    engine = Mock(side_effect=AssertionError("No engine may be created"))
    monkeypatch.setattr(verifier, "create_async_engine", engine)
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(
        verifier,
        "settings",
        SimpleNamespace(DATABASE_URL=database_url, REDIS_URL=redis_url),
    )
    with pytest.raises(RuntimeError, match="Only loopback infrastructure"):
        await verifier.main()
    engine.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("E:/code/sample-workflow/config.py", False),
        ("postgresql://user:sample@127.0.0.1/test", True),
        ('{"POSTGRES_PASSWORD": "sample"}', True),
        ("password='sample' host=127.0.0.1", True),
        ("postgresql://user:p%40ss%2Fword@127.0.0.1/test", True),
    ],
)
def test_password_detection_uses_credential_context(content, expected):
    verifier = load_verifier()
    password = "p@ss/word" if "p%40ss" in content else "sample"
    url = make_url("postgresql://user@127.0.0.1/test").set(password=password)
    cleaned, detected = verifier.redact_connection_secrets(content, url)
    assert detected is expected
    if expected:
        assert "[REDACTED]" in cleaned
        assert password not in cleaned
    else:
        assert cleaned == content
