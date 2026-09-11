"""SQL debug output must never reveal provider credentials or customer values."""

from types import SimpleNamespace
from unittest.mock import Mock

from src.rag_service import database


def test_engine_hides_bound_parameters_even_in_debug(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(database, "create_async_engine", factory)
    monkeypatch.setattr(database, "engine", None)
    monkeypatch.setattr(database, "get_settings", lambda: SimpleNamespace(
        debug=True, environment="test", database_url="postgresql+asyncpg://fixture"
    ))
    database.create_database_engine()
    assert factory.call_args.kwargs["echo"] is True
    assert factory.call_args.kwargs["hide_parameters"] is True
