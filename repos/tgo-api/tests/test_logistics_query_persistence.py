"""Live-query failures must not invent or overwrite durable logistics facts."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.customer_logistics import CustomerShipment, LogisticsSettings, ShipmentTrackingEvent
from app.services.customer_logistics_service import CustomerLogisticsService
from app.utils.crypto import encrypt_str


@pytest.fixture
def archive():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (LogisticsSettings, CustomerShipment, ShipmentTrackingEvent):
        model.__table__.create(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        project, visitor, shipment_id = uuid4(), uuid4(), uuid4()
        db.add(LogisticsSettings(project_id=project, query_tool_id=uuid4()))
        shipment = CustomerShipment(
            id=shipment_id,
            project_id=project,
            visitor_id=visitor,
            tracking_no_ciphertext=encrypt_str("SF1234567890"),
            tracking_no_hash="a" * 64,
            tracking_no_masked="SF12****7890",
            source="visitor_message",
            verification_state="pending",
            status="in_transit",
            latest_summary="已有轨迹",
            last_checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        db.add(shipment)
        db.commit()
        yield CustomerLogisticsService(db), shipment, factory
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        {},
        {"success": True, "output_data": {}},
        {"success": True, "output_data": {"isError": True, "content": "provider secret"}},
        {"success": True, "output_data": {"tracking_no": "SF9999999999", "status": "delivered"}},
    ],
)
async def test_invalid_result_keeps_previous_summary_and_returns_safe_error(archive, monkeypatch, output):
    service, shipment, _ = archive
    monkeypatch.setattr(
        "app.services.customer_logistics_service.ai_client.execute_tool", AsyncMock(return_value=output)
    )
    with pytest.raises(HTTPException) as error:
        await service.query_shipment(shipment.project_id, shipment.id)
    assert error.value.status_code == 502
    assert "secret" not in str(error.value.detail)
    service.db.refresh(shipment)
    assert shipment.latest_summary == "已有轨迹"
    assert shipment.status == "in_transit"
    assert shipment.verification_state == "pending"
    assert shipment.last_checked_at.year == 2026 and shipment.last_checked_at.month == 1
    assert service.db.query(ShipmentTrackingEvent).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("with_event", [True, False])
async def test_delivered_time_comes_from_carrier_not_query_clock(archive, monkeypatch, with_event):
    service, shipment, _ = archive
    output = {"status": "delivered", "summary": "本人已签收"}
    if with_event:
        output["traces"] = [{"status": "delivered", "context": "本人已签收", "time": "2026-09-01T10:00:00Z"}] * 2
    query = AsyncMock(return_value={"success": True, "output_data": output})
    monkeypatch.setattr("app.services.customer_logistics_service.ai_client.execute_tool", query)
    for _ in range(2):
        result, events = await service.query_shipment(shipment.project_id, shipment.id)
    assert len(events) == int(with_event)
    if with_event:
        assert result.delivered_at.replace(tzinfo=timezone.utc) == datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    else:
        assert result.delivered_at is None


@pytest.mark.asyncio
async def test_conflict_is_not_erased_by_query_and_tool_is_not_called(archive, monkeypatch):
    service, shipment, _ = archive
    shipment.verification_state = "conflict"
    service.db.commit()
    query = AsyncMock(return_value={"success": True, "output_data": {"status": "delivered"}})
    monkeypatch.setattr("app.services.customer_logistics_service.ai_client.execute_tool", query)
    with pytest.raises(HTTPException) as error:
        await service.query_shipment(shipment.project_id, shipment.id)
    assert error.value.status_code == 409
    assert shipment.verification_state == "conflict"
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_older_inflight_result_does_not_overwrite_newer_query(archive, monkeypatch):
    service, shipment, factory = archive

    async def late_result(**_):
        with factory() as concurrent:
            newer = concurrent.get(CustomerShipment, shipment.id)
            newer.latest_summary = "较新的查询结果"
            newer.last_checked_at = datetime.now(timezone.utc)
            concurrent.commit()
        return {"success": True, "output_data": {"status": "delivered", "summary": "旧结果"}}

    monkeypatch.setattr("app.services.customer_logistics_service.ai_client.execute_tool", late_result)
    result, _ = await service.query_shipment(shipment.project_id, shipment.id)
    assert result.latest_summary == "较新的查询结果"
    assert result.status == "in_transit"


def test_invalid_tracking_number_is_a_user_input_error(archive):
    service, shipment, _ = archive
    with pytest.raises(HTTPException) as error:
        service.create_shipment(
            project_id=shipment.project_id, visitor_id=shipment.visitor_id, tracking_no="abcdefgh", source="manual"
        )
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_conflict_found_during_provider_call_is_not_erased(archive, monkeypatch):
    service, shipment, factory = archive

    async def result_after_conflict(**_):
        with factory() as concurrent:
            concurrent.get(CustomerShipment, shipment.id).verification_state = "conflict"
            concurrent.commit()
        return {"success": True, "output_data": {"status": "delivered"}}

    monkeypatch.setattr("app.services.customer_logistics_service.ai_client.execute_tool", result_after_conflict)
    with pytest.raises(HTTPException) as error:
        await service.query_shipment(shipment.project_id, shipment.id)
    assert error.value.status_code == 409
    service.db.refresh(shipment)
    assert shipment.verification_state == "conflict"
    assert shipment.latest_summary == "已有轨迹"


@pytest.mark.asyncio
async def test_later_started_query_can_publish_after_an_earlier_query_completed(archive, monkeypatch):
    service, shipment, factory = archive

    async def earlier_finished(**_):
        with factory() as concurrent:
            earlier = concurrent.get(CustomerShipment, shipment.id)
            earlier.latest_summary = "较早启动的结果"
            earlier.last_checked_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
            concurrent.commit()
        return {"success": True, "output_data": {"status": "in_transit", "summary": "当前查询结果"}}

    monkeypatch.setattr("app.services.customer_logistics_service.ai_client.execute_tool", earlier_finished)
    result, _ = await service.query_shipment(shipment.project_id, shipment.id)
    assert result.latest_summary == "当前查询结果"
