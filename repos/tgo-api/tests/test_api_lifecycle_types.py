"""Exercise local task lifecycles and GeoIP without external services."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import geoip2.errors
import pytest

from app.services.geoip_service import GeoIPService, GeoLocation
from app.tasks import close_timeout_sessions, sync_visitor_online_status


TASKS = (
    (close_timeout_sessions, "SESSION_TIMEOUT_CHECK_ENABLED",
     "SESSION_TIMEOUT_CHECK_INTERVAL_SECONDS", "_process_timeout_sessions",
     "start_session_timeout_task", "stop_session_timeout_task"),
    (sync_visitor_online_status, "VISITOR_ONLINE_SYNC_ENABLED",
     "VISITOR_ONLINE_SYNC_INTERVAL_SECONDS", "_process_online_status_sync",
     "start_visitor_online_sync_task", "stop_visitor_online_sync_task"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("module, enabled, interval, process, start, stop",
                         TASKS)
async def test_background_task_starts_once_and_cancels_cleanly(
    monkeypatch, module, enabled, interval, process, start, stop,
):
    monkeypatch.setattr(module, "_task", None)
    monkeypatch.setattr(module, "_processing_lock", asyncio.Lock())
    monkeypatch.setattr(module.settings, enabled, True)
    monkeypatch.setattr(module.settings, interval, 60)
    processed = asyncio.Event()
    calls = 0

    async def process_once():
        nonlocal calls
        calls += 1
        processed.set()
        return 1

    monkeypatch.setattr(module, process, process_once)
    starter, stopper = getattr(module, start), getattr(module, stop)
    try:
        assert await starter() is None
        original_task = module._task
        assert original_task is not None
        assert await starter() is None
        assert module._task is original_task
        await asyncio.wait_for(processed.wait(), timeout=1)
        assert calls == 1
        assert await asyncio.wait_for(stopper(), timeout=1) is None
        assert original_task.cancelled()
        assert module._task is None
        assert await stopper() is None
    finally:
        await stopper()


@pytest.mark.asyncio
@pytest.mark.parametrize("module, enabled, interval, process, start, stop",
                         TASKS)
async def test_disabled_background_task_does_not_create_a_worker(
    monkeypatch, module, enabled, interval, process, start, stop,
):
    monkeypatch.setattr(module, "_task", None)
    monkeypatch.setattr(module.settings, enabled, False)
    assert await getattr(module, start)() is None
    assert module._task is None
    assert await getattr(module, stop)() is None


def test_missing_geoip_reader_returns_empty_location_without_lookup_error(
    caplog,
):
    service = GeoIPService()
    assert service._lookup_geoip2("203.0.113.10") == GeoLocation()
    assert "GeoIP2 lookup failed" not in caplog.text


@pytest.mark.parametrize("with_region", [True, False])
def test_geoip_response_preserves_country_city_and_optional_region(
    with_region,
):
    service = GeoIPService()
    response = SimpleNamespace(
        country=SimpleNamespace(name="测试国家", iso_code="XX"),
        city=SimpleNamespace(name="测试城市"),
        subdivisions=(
            SimpleNamespace(most_specific=SimpleNamespace(name="测试地区"))
            if with_region else []
        ),
    )
    reader = Mock()
    reader.city.return_value = response
    service._geoip2_reader = reader
    assert service._lookup_geoip2("203.0.113.10") == GeoLocation(
        country="测试国家", country_code="XX", city="测试城市",
        region="测试地区" if with_region else None,
    )
    reader.city.assert_called_once_with("203.0.113.10")


@pytest.mark.parametrize("error", [
    geoip2.errors.AddressNotFoundError("synthetic missing address"),
    ValueError("synthetic database error"),
])
def test_geoip_lookup_failure_remains_an_empty_location(error):
    service = GeoIPService()
    reader = Mock()
    reader.city.side_effect = error
    service._geoip2_reader = reader
    assert service._lookup_geoip2("203.0.113.10") == GeoLocation()


def test_geoip_close_releases_each_reader_once():
    service = GeoIPService()
    readers = [Mock(), Mock(), Mock()]
    (service._geoip2_reader, service._ip2region_searcher_v4,
     service._ip2region_searcher_v6) = readers
    assert service.close() is None
    assert service.close() is None
    for reader in readers:
        reader.close.assert_called_once_with()
    assert service._geoip2_reader is None
    assert service._ip2region_searcher_v4 is None
    assert service._ip2region_searcher_v6 is None
