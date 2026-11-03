"""The restore verifier must never construct a source or remote target URL."""

import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).with_name("workflow-timer-migration-rehearsal.py")
SPEC = importlib.util.spec_from_file_location("timer_migration_rehearsal", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)
OWNED = "tgo_restore_" + "a" * 32


@pytest.mark.parametrize("source, target", [
    ("postgresql+asyncpg://test@foreign.example/source", OWNED),
    ("postgresql+asyncpg://test@127.0.0.1/source", "source"),
    ("postgresql+asyncpg://test@127.0.0.1/source", "tgo_restore_../source"),
    ("postgresql+asyncpg://test@127.0.0.1/" + OWNED, OWNED),
])
def test_unsafe_or_source_database_is_rejected(source, target):
    with pytest.raises(ValueError):
        module.owned_url(source, target)


def test_owned_copy_keeps_connection_settings_without_changing_source():
    source = "postgresql+asyncpg://test@127.0.0.1:15432/source"
    target = module.owned_url(source, OWNED)
    assert target.database == OWNED
    assert target.host == "127.0.0.1" and target.port == 15432
    assert source.endswith("/source")
