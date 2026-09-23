"""Failed or unpriced calls remain visible and cannot appear free."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
from uuid import uuid4

import pytest

from app.config import settings
from app.schemas.model_usage import ModelCostRate
from app.services import model_usage
from app.services.quota_authorization import (
    UsageAuthorization,
    current_authorization,
    metered_execution,
)


def test_cost_uses_exact_configured_fen_rates_and_unknown_is_not_zero():
    rate = ModelCostRate(input_fen_per_million="100", output_fen_per_million="200")
    assert model_usage.estimate_cost(rate, 1000, 2000) == Decimal("0.50000000")
    assert model_usage.estimate_cost(None, 1000, 2000) is None
    assert model_usage.estimate_cost(rate, None, 2000) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_model_call_persists_start_and_one_completion(monkeypatch, fail):
    authorization = UsageAuthorization(
        project_id=uuid4(), reservation_id=uuid4(), lease_id=uuid4()
    )
    quota_token = current_authorization.set(authorization)
    metered_token = metered_execution.set(True)
    monkeypatch.setattr(settings, "saas_enabled", True)
    monkeypatch.setattr(settings, "saas_billing_enabled", True)
    started, finished = AsyncMock(), AsyncMock()
    monkeypatch.setattr(model_usage, "save_started", started)
    monkeypatch.setattr(model_usage, "save_finished", finished)
    try:
        try:
            async with model_usage.track_model_usage(
                str(authorization.project_id), "synthetic-model", "standard"
            ) as tracker:
                started.assert_awaited_once()
                tracker.observe(SimpleNamespace(input_tokens=10, output_tokens=20))
                tracker.observe(SimpleNamespace(input_tokens=10, output_tokens=20))
                if fail:
                    raise ValueError("synthetic")
        except ValueError:
            assert fail
        finished.assert_awaited_once()
        observed = finished.call_args.args[1]
        assert observed.input_tokens == 10 and observed.output_tokens == 20
        assert observed.status == ("failed" if fail else "succeeded")
        assert started.call_args.args[0].reservation_id == authorization.reservation_id
    finally:
        current_authorization.reset(quota_token)
        metered_execution.reset(metered_token)


@pytest.mark.asyncio
async def test_disabled_billing_never_writes_cost_tables(monkeypatch):
    monkeypatch.setattr(settings, "saas_billing_enabled", False)
    save = AsyncMock()
    monkeypatch.setattr(model_usage, "save_started", save)
    async with model_usage.track_model_usage(str(uuid4()), "synthetic", "standard"):
        pass
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_cost_inventory_failure_prevents_model_start(monkeypatch):
    auth = UsageAuthorization(
        project_id=uuid4(), reservation_id=uuid4(), lease_id=uuid4()
    )
    authorization_token = current_authorization.set(auth)
    metered_token = metered_execution.set(True)
    monkeypatch.setattr(settings, "saas_enabled", True)
    monkeypatch.setattr(settings, "saas_billing_enabled", True)
    monkeypatch.setattr(
        model_usage, "save_started", AsyncMock(side_effect=RuntimeError("unavailable"))
    )
    invoked = False
    try:
        with pytest.raises(RuntimeError, match="unavailable"):
            async with model_usage.track_model_usage(
                str(auth.project_id), "synthetic", "standard"
            ):
                invoked = True
        assert not invoked
    finally:
        current_authorization.reset(authorization_token)
        metered_execution.reset(metered_token)


@pytest.mark.asyncio
async def test_cancellation_is_recorded_without_inventing_token_counts(monkeypatch):
    auth = UsageAuthorization(
        project_id=uuid4(), reservation_id=uuid4(), lease_id=uuid4()
    )
    authorization_token = current_authorization.set(auth)
    metered_token = metered_execution.set(True)
    monkeypatch.setattr(settings, "saas_enabled", True)
    monkeypatch.setattr(settings, "saas_billing_enabled", True)
    monkeypatch.setattr(model_usage, "save_started", AsyncMock())
    finished = AsyncMock()
    monkeypatch.setattr(model_usage, "save_finished", finished)
    try:
        with pytest.raises(asyncio.CancelledError):
            async with model_usage.track_model_usage(
                str(auth.project_id), "synthetic", "standard"
            ):
                raise asyncio.CancelledError()
        assert finished.call_args.args[1].status == "cancelled"
        assert finished.call_args.args[1].input_tokens is None
    finally:
        current_authorization.reset(authorization_token)
        metered_execution.reset(metered_token)
