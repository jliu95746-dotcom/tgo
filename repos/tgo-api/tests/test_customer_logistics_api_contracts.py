"""Public logistics responses preserve scope and hide stored identifiers."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, DateTime, MetaData, Table, Uuid, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import customer_logistics
from app.models.customer_logistics import (
    CustomerShipment,
    LogisticsSettings,
    ShipmentTrackingEvent,
)
from app.services.customer_logistics_service import tracking_hash
from app.services.logistics_result_parser import ParsedTrackingResult
from app.utils.crypto import encrypt_str


@pytest.fixture
def archive(authenticated_project, db_override):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    # Only these three visitor columns are read by the existence check.
    visitors = Table(
        "api_visitors", MetaData(),
        Column("id", Uuid, primary_key=True),
        Column("project_id", Uuid, nullable=False),
        Column("deleted_at", DateTime(timezone=True)),
    )
    visitors.create(engine)
    for model in (LogisticsSettings, CustomerShipment, ShipmentTrackingEvent):
        model.__table__.create(engine)
    with Session(engine, autoflush=False) as db:
        own_visitor, other_visitor, deleted_visitor = (
            uuid4(), uuid4(), uuid4()
        )
        project_id, other_project = authenticated_project.id, uuid4()
        clock = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        db.execute(visitors.insert(), [
            {"id": own_visitor, "project_id": project_id,
             "deleted_at": None},
            {"id": other_visitor, "project_id": other_project,
             "deleted_at": None},
            {"id": deleted_visitor, "project_id": project_id,
             "deleted_at": clock},
        ])
        own = CustomerShipment(
            id=uuid4(), project_id=project_id, visitor_id=own_visitor,
            tracking_no_ciphertext=encrypt_str("SF1234567890"),
            tracking_no_hash="a" * 64,
            tracking_no_masked="SF12****7890", carrier_code="SF",
            carrier_name="测试快递", status="in_transit", source="manual",
            verification_state="pending", latest_summary="运输中",
            last_checked_at=clock, created_at=clock, updated_at=clock,
        )
        foreign = CustomerShipment(
            id=uuid4(), project_id=other_project, visitor_id=other_visitor,
            tracking_no_ciphertext=encrypt_str("SF9999999999"),
            tracking_no_hash="b" * 64,
            tracking_no_masked="SF99****9999", source="manual",
            status="in_transit", verification_state="pending",
            created_at=clock, updated_at=clock,
        )
        tool_id = uuid4()
        db.add_all([
            own, foreign,
            LogisticsSettings(project_id=project_id, query_tool_id=tool_id),
        ])
        db.flush()
        earlier, later = uuid4(), uuid4()
        db.add_all([
            ShipmentTrackingEvent(
                id=earlier, shipment_id=own.id, status="in_transit",
                description="包裹已揽收", location=None, event_time=clock,
            ),
            ShipmentTrackingEvent(
                id=later, shipment_id=own.id, status="in_transit",
                description="包裹运输中", location="测试分拨中心",
                event_time=datetime(2026, 1, 2, 12, tzinfo=timezone.utc),
            ),
            ShipmentTrackingEvent(
                shipment_id=foreign.id, description="其他企业的包裹",
                event_time=clock,
            ),
        ])
        db.commit()
        db_override.session = db
        yield db, own, foreign, deleted_visitor, earlier, later, tool_id
        db_override.session = None
    engine.dispose()


def assert_public_shipment(data, shipment):
    assert set(data) == {
        "id", "visitor_id", "tracking_no_masked", "carrier_code",
        "carrier_name", "status", "source", "verification_state",
        "latest_summary", "last_checked_at", "delivered_at", "archived_at",
        "created_at", "updated_at",
    }
    assert data["id"] == str(shipment.id)
    assert data["visitor_id"] == str(shipment.visitor_id)
    assert data["tracking_no_masked"] == shipment.tracking_no_masked
    assert data["status"] == shipment.status
    assert data["verification_state"] == shipment.verification_state


def test_list_returns_only_the_requested_customers_public_archive(
    client, archive,
):
    _, own, foreign, _, _, _, _ = archive
    response = client.get(f"/v1/logistics/visitors/{own.visitor_id}/shipments")
    assert response.status_code == 200
    records = response.json()["shipments"]
    assert len(records) == 1
    assert_public_shipment(records[0], own)
    assert str(foreign.id) not in response.text
    assert "SF1234567890" not in response.text
    assert own.tracking_no_ciphertext not in response.text
    assert own.tracking_no_hash not in response.text


@pytest.mark.parametrize("target", ["unknown", "deleted", "foreign"])
@pytest.mark.parametrize("method", ["get", "post"])
def test_visitor_check_rejects_missing_deleted_and_foreign_customers(
    client, archive, target, method,
):
    db, _, foreign, deleted, _, _, _ = archive
    visitor = {
        "unknown": uuid4(), "deleted": deleted,
        "foreign": foreign.visitor_id,
    }[target]
    before = db.query(CustomerShipment).count()
    path = f"/v1/logistics/visitors/{visitor}/shipments"
    response = (
        client.post(path, json={"tracking_no": "SF8888888888"})
        if method == "post" else client.get(path)
    )
    assert response.status_code == 404
    assert db.query(CustomerShipment).count() == before


def test_create_then_repeat_returns_the_same_public_archive(client, archive):
    db, own, _, _, _, _, _ = archive
    path = f"/v1/logistics/visitors/{own.visitor_id}/shipments"
    for _ in range(2):
        response = client.post(path, json={
            "tracking_no": "SF8888888888",
            "carrier_code": "SF", "carrier_name": "测试快递",
        })
        assert response.status_code == 201
        body = response.json()
        saved = db.query(CustomerShipment).filter_by(
            tracking_no_hash=tracking_hash("SF8888888888"),
        ).one()
        assert_public_shipment(body["shipment"], saved)
        assert body["shipment"]["tracking_no_masked"] == "SF88****8888"
        assert body["shipment"]["status"] == "unknown"
        assert body["events"] == []
        assert body["queried_live"] is False
        assert body["message"] == "物流单已加入顾客档案"
        assert "SF8888888888" not in response.text
    assert db.query(CustomerShipment).count() == 3


def test_events_remain_ordered_and_expose_only_declared_fields(
    client, archive,
):
    _, own, _, _, earlier, later, _ = archive
    response = client.get(f"/v1/logistics/shipments/{own.id}/events")
    assert response.status_code == 200
    assert response.json() == {"events": [
        {"id": str(later), "shipment_id": str(own.id),
         "status": "in_transit", "description": "包裹运输中",
         "location": "测试分拨中心", "event_time": "2026-01-02T12:00:00"},
        {"id": str(earlier), "shipment_id": str(own.id),
         "status": "in_transit", "description": "包裹已揽收",
         "location": None, "event_time": "2026-01-01T12:00:00"},
    ]}


def test_empty_archives_and_events_still_return_json_arrays(client, archive):
    db, own, _, _, _, _, _ = archive
    db.query(ShipmentTrackingEvent).filter_by(shipment_id=own.id).delete()
    db.commit()
    response = client.get(f"/v1/logistics/shipments/{own.id}/events")
    assert response.status_code == 200
    assert response.json() == {"events": []}
    db.delete(own)
    db.commit()
    response = client.get(f"/v1/logistics/visitors/{own.visitor_id}/shipments")
    assert response.status_code == 200
    assert response.json() == {"shipments": []}


@pytest.mark.parametrize("target", ["foreign", "unknown"])
@pytest.mark.parametrize("action", ["events", "query"])
def test_query_and_events_deny_other_projects_before_calling_tools(
    client, archive, monkeypatch, target, action,
):
    _, _, foreign, _, _, _, _ = archive
    query = AsyncMock()
    monkeypatch.setattr(
        "app.services.customer_logistics_service.ai_client.execute_tool",
        query,
    )
    shipment_id = foreign.id if target == "foreign" else uuid4()
    path = f"/v1/logistics/shipments/{shipment_id}/{action}"
    response = client.post(path) if action == "query" else client.get(path)
    assert response.status_code == 404
    query.assert_not_awaited()


def test_query_preserves_provider_input_and_returns_persisted_public_result(
    client, archive, monkeypatch,
):
    db, own, _, _, _, _, tool_id = archive
    query = AsyncMock(return_value={"success": True, "output_data": {
        "tracking_no": "SF1234567890", "status": "delivered",
        "summary": "已签收", "traces": [{
            "context": "本人已签收", "status": "delivered",
            "time": "2026-01-03T12:00:00Z",
        }],
    }})
    monkeypatch.setattr(
        "app.services.customer_logistics_service.ai_client.execute_tool",
        query,
    )
    response = client.post(f"/v1/logistics/shipments/{own.id}/query")
    assert response.status_code == 200
    query.assert_awaited_once_with(
        project_id=str(own.project_id), tool_id=str(tool_id),
        input_data={"tracking_no": "SF1234567890"},
        visitor_id=str(own.visitor_id),
    )
    db.refresh(own)
    body = response.json()
    assert_public_shipment(body["shipment"], own)
    assert body["shipment"]["status"] == "delivered"
    assert body["shipment"]["latest_summary"] == "已签收"
    assert body["queried_live"] is True
    assert body["message"] == "物流轨迹已更新"
    assert len(body["events"]) == 3
    assert body["events"][0]["description"] == "本人已签收"
    assert "SF1234567890" not in response.text


@pytest.mark.parametrize("details, expected", [
    ({}, {}),
    ({"carrier_code": None, "phone": None}, {}),
    ({"carrier_code": "SF"}, {"carrier_code": "SF"}),
    ({"phone": "1234"}, {"phone": "1234"}),
    ({"phone": "12345678901"}, {"phone": "12345678901"}),
    ({"carrier_code": "SF", "phone": "1234"},
     {"carrier_code": "SF", "phone": "1234"}),
])
def test_optional_verification_fields_keep_omission_and_explicit_values(
    client, authenticated_project, monkeypatch, details, expected,
):
    query = AsyncMock(return_value=ParsedTrackingResult(
        "in_transit", None, None, "运输中", (),
    ))
    service = type("TestLogisticsService", (), {"execute_live_query": query})()
    monkeypatch.setattr(customer_logistics, "_service", lambda _: service)
    selected = uuid4()
    response = client.post("/v1/logistics/settings/test", json={
        "tracking_no": "SF1234567890", "query_tool_id": str(selected),
        **details,
    })
    assert response.status_code == 200
    assert response.json() == {
        "success": True, "message": "快递查询工具连接成功", "preview": "运输中",
    }
    query.assert_awaited_once_with(
        authenticated_project.id, "SF1234567890",
        query_tool_id=selected, **expected,
    )
