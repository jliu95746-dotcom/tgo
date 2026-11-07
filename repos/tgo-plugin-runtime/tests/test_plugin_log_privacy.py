"""Untrusted process output remains useful without exposing private values."""

import asyncio
import json
import sys
from types import SimpleNamespace
from urllib.parse import quote
from unittest.mock import AsyncMock, Mock
from datetime import datetime, UTC
from uuid import uuid4

import pytest

from app.services.process_manager import ManagedPlugin, ProcessManager
from app.services import process_manager as process_module
from app.api import routes

PRIVATE = "synthetic-plugin-private-value-349"


def managed_plugin():
    return ManagedPlugin(
        id="fixture",
        config={"runtime": {"env": {"API_TOKEN": PRIVATE}}},
    )


async def collect(managed, chunks):
    manager = ProcessManager()
    manager._managed_plugins[managed.id] = managed
    reader = asyncio.StreamReader()
    for chunk in chunks:
        reader.feed_data(chunk)
    reader.feed_eof()
    await manager._read_logs(managed, SimpleNamespace(stdout=reader))
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "line",
    [
        PRIVATE,
        f"Authorization: Bearer {PRIVATE}",
        f'{{"password": "{PRIVATE}"}}',
        f"https://user:{PRIVATE}@source.test/config?token={PRIVATE}",
        "customer_email=test.person@example.test",
        "phone=13800000000",
        "id_card=110101199001010000",
    ],
)
async def test_output_is_scrubbed_before_it_enters_the_log_buffer(line):
    managed = managed_plugin()
    manager = await collect(managed, [f"ready\n{line}\ndone\n".encode()])
    output = "\n".join(managed.logs)
    for value in [
        PRIVATE,
        "test.person@example.test",
        "13800000000",
        "110101199001010000",
    ]:
        assert value not in output
    assert manager.get_logs("fixture")[0] == "ready"
    assert manager.get_logs("fixture")[-1] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["raw", "json", "url"])
async def test_configured_private_values_survive_no_encoding_escape(encoding):
    value = "synthetic secret/+中文349"
    managed = ManagedPlugin(
        id="fixture", config={"runtime": {"env": {"CUSTOM_VALUE": value}}}
    )
    rendered = {
        "raw": value,
        "json": json.dumps(value)[1:-1],
        "url": quote(value, safe=""),
    }[encoding]
    await collect(managed, [(rendered + "\n").encode()])
    assert rendered not in "\n".join(managed.logs)


@pytest.mark.asyncio
async def test_child_environment_secrets_are_hidden(monkeypatch):
    monkeypatch.setenv("PLUGIN_SERVICE_TOKEN", PRIVATE)
    managed = ManagedPlugin(id="fixture", config={})
    await collect(managed, [(PRIVATE + "\n").encode()])
    assert PRIVATE not in "\n".join(managed.logs)


@pytest.mark.asyncio
async def test_oversized_lines_are_discarded_and_reading_continues():
    managed = managed_plugin()
    manager = await collect(
        managed, [b"x" * 100_000, PRIVATE.encode(), b"\nready\n"]
    )
    assert sum(len(line) for line in managed.logs) < 1000
    assert PRIVATE not in "\n".join(managed.logs)
    assert manager.get_logs("fixture")[-1] == "ready"


def test_old_buffer_and_error_are_scrubbed_on_read_without_exposing_config():
    managed = managed_plugin()
    managed.logs.extend(["ready", PRIVATE])
    managed.last_error = PRIVATE
    manager = ProcessManager()
    manager._managed_plugins[managed.id] = managed
    assert PRIVATE not in repr(managed)
    assert PRIVATE not in "\n".join(manager.get_logs("fixture"))
    assert PRIVATE not in manager.get_status("fixture")["last_error"]
    assert manager.get_logs("missing") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["x" * 9000, None])
async def test_unbounded_private_configuration_hides_output(value):
    env = (
        {"TOKEN": value}
        if value
        else {
            f"VALUE_{index}": f"private-value-{index}" for index in range(140)
        }
    )
    managed = ManagedPlugin(id="fixture", config={"runtime": {"env": env}})
    await collect(managed, [b"private-value-139\nxxxxxxxxxxxxxxxxxxxx\n"])
    output = "\n".join(managed.logs)
    assert "private-value-139" not in output and "x" * 20 not in output


@pytest.mark.asyncio
async def test_protection_budget_is_independent_for_successive_processes():
    managed = managed_plugin()
    for index in range(6):
        managed.config = {
            "runtime": {
                "env": {
                    "API_TOKEN": str(index) + "s" * 6000,
                }
            }
        }
        await collect(managed, [b"ready\n"])
        assert managed.logs[-1] == "ready"


@pytest.mark.asyncio
async def test_old_output_keeps_its_policy_after_a_new_process_starts():
    managed = managed_plugin()
    reader = asyncio.StreamReader()
    task = asyncio.create_task(
        ProcessManager()._read_logs(
            managed,
            SimpleNamespace(stdout=reader),
        )
    )
    await asyncio.sleep(0)
    managed.config = {"runtime": {"env": {"TOKEN": "new-secret-value-349"}}}
    await collect(managed, [b"new-secret-value-349\n"])
    reader.feed_data((PRIVATE + "\n").encode())
    reader.feed_eof()
    await asyncio.wait_for(task, timeout=2)
    assert PRIVATE not in "\n".join(managed.logs)
    assert "new-secret-value-349" not in "\n".join(managed.logs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "line", ["test.person@example.test", "13800000000", "110101199001010000"]
)
async def test_unlabelled_contact_information_is_hidden(line):
    managed = managed_plugin()
    await collect(managed, [(line + "\n").encode()])
    assert line not in "\n".join(managed.logs)


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [["--token", "abc"], ["--api-key=abc"]])
async def test_short_credential_arguments_are_protected(args):
    managed = ManagedPlugin(id="fixture", config={"runtime": {"args": args}})
    await collect(managed, [b"value abc\n"])
    assert "abc" not in "\n".join(managed.logs)


@pytest.mark.asyncio
async def test_control_sequences_cannot_split_a_known_private_value():
    managed = managed_plugin()
    disguised = PRIVATE[:10] + "\x1b[31m" + PRIVATE[10:]
    await collect(managed, [(disguised + "\n").encode()])
    assert PRIVATE not in "\n".join(managed.logs)
    assert "\x1b" not in "\n".join(managed.logs)


@pytest.mark.asyncio
async def test_normal_unicode_dates_and_unterminated_output_are_preserved():
    managed = managed_plugin()
    normal = "2026-11-07 服务已启动"
    await collect(managed, [(normal + "\nready").encode()])
    assert list(managed.logs) == [normal, "ready"]


@pytest.mark.asyncio
async def test_long_non_private_diagnostics_below_the_limit_are_preserved():
    managed = managed_plugin()
    normal = "abc" * 4000
    await collect(managed, [(normal + "\nready\n").encode()])
    assert list(managed.logs) == [normal, "ready"]


@pytest.mark.asyncio
async def test_output_read_failure_has_no_raw_exception(caplog):
    managed = managed_plugin()
    await ProcessManager()._read_logs(
        managed,
        SimpleNamespace(
            stdout=SimpleNamespace(
                read=AsyncMock(side_effect=OSError(PRIVATE)),
            )
        ),
    )
    assert PRIVATE not in "\n".join(managed.logs)
    assert PRIVATE not in "\n".join(str(r.__dict__) for r in caplog.records)


@pytest.mark.asyncio
async def test_cancelled_output_reader_stops_without_logging_pending_data():
    managed = managed_plugin()
    reader = asyncio.StreamReader()
    reader.feed_data(PRIVATE[:10].encode())
    task = asyncio.create_task(
        ProcessManager()._read_logs(
            managed,
            SimpleNamespace(stdout=reader),
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not managed.logs


@pytest.mark.asyncio
async def test_owned_process_stdout_uses_its_original_environment(
    tmp_path,
    monkeypatch,
):
    manager = ProcessManager(str(tmp_path))
    target = tmp_path / "fixture"
    target.mkdir()
    (target / "probe.py").write_text(
        "import os,time\nprint('ready',flush=True)\n"
        "print(os.environ['PLUGIN_SERVICE_TOKEN'],flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PLUGIN_SERVICE_TOKEN", PRIVATE)
    monkeypatch.setattr(
        process_module, "plugin_python", lambda _: sys.executable
    )
    monkeypatch.setattr(manager, "_update_db_status", AsyncMock())
    monkeypatch.setattr(
        process_module.plugin_manager,
        "get_plugin",
        Mock(
            return_value=SimpleNamespace(
                writer=SimpleNamespace(
                    is_closing=lambda: False,
                )
            ),
        ),
    )
    monkeypatch.setattr(
        process_module.plugin_manager, "unregister", AsyncMock()
    )
    try:
        success, _ = await manager.start_plugin(
            "fixture",
            {
                "build": {
                    "language": "python",
                    "python": {"entrypoint": "probe.py"},
                },
            },
        )
        assert success
        monkeypatch.setenv("PLUGIN_SERVICE_TOKEN", "new-value-after-start")

        async def wait_output():
            while len(manager.get_logs("fixture")) < 2:
                await asyncio.sleep(0.02)

        await asyncio.wait_for(wait_output(), timeout=10)
        assert manager.get_logs("fixture")[0] == "ready"
        assert PRIVATE not in "\n".join(
            manager._managed_plugins["fixture"].logs
        )
    finally:
        await manager.stop_plugin("fixture")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored", [None, "", "Plugin startup failed", PRIVATE]
)
async def test_installed_list_does_not_expose_historically_stored_errors(
    monkeypatch,
    stored,
):
    project = str(uuid4())
    row = SimpleNamespace(
        id=uuid4(),
        plugin_id="fixture",
        project_id=project,
        name="Fixture",
        version="1",
        description=None,
        author=None,
        status="error",
        pid=None,
        install_type="binary",
        source_url=None,
        latest_version="1",
        installed_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        last_error=stored,
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, statement):
            assert project in statement.compile().params.values()
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [row])
            )

    monkeypatch.setattr(routes, "AsyncSessionLocal", Session)
    monkeypatch.setattr(
        routes.process_manager,
        "get_status",
        lambda _: {
            "status": "not_managed",
        },
    )
    monkeypatch.setattr(routes.plugin_manager, "get_plugin", lambda _: None)
    monkeypatch.setattr(routes.plugin_manager, "_plugins", {})
    result = await routes.list_installed_plugins(project)
    assert PRIVATE not in result.model_dump_json()
    assert row.last_error == stored
    if stored != PRIVATE:
        assert result.plugins[0].last_error == stored
