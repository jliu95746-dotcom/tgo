"""Authenticated customer logistics archive endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_authenticated_project
from app.models import Visitor
from app.schemas.customer_logistics import (
    LogisticsSettingsResponse,
    LogisticsSettingsUpdate,
    LogisticsToolTestRequest,
    LogisticsToolTestResponse,
    ShipmentCreateRequest,
    ShipmentListResponse,
    ShipmentQueryResponse,
    TrackingEventListResponse,
)
from app.services.customer_logistics_service import CustomerLogisticsService


router = APIRouter()


def _service(db: Session) -> CustomerLogisticsService:
    return CustomerLogisticsService(db)


def _ensure_visitor(db: Session, project_id: UUID, visitor_id: UUID) -> None:
    exists = (
        db.query(Visitor.id)
        .filter(
            Visitor.id == visitor_id,
            Visitor.project_id == project_id,
            Visitor.deleted_at.is_(None),
        )
        .first()
    )
    if exists is None:
        raise HTTPException(status_code=404, detail="顾客不存在")


@router.get("/settings", response_model=LogisticsSettingsResponse)
def get_settings(
    db: Session = Depends(get_db),
    project_and_api_key=Depends(get_authenticated_project),
) -> LogisticsSettingsResponse:
    project, _ = project_and_api_key
    return LogisticsSettingsResponse.model_validate(
        _service(db).get_settings(project.id)
    )


@router.put("/settings", response_model=LogisticsSettingsResponse)
def update_settings(
    update: LogisticsSettingsUpdate,
    db: Session = Depends(get_db),
    project_and_api_key=Depends(get_authenticated_project),
) -> LogisticsSettingsResponse:
    project, _ = project_and_api_key
    return LogisticsSettingsResponse.model_validate(
        _service(db).update_settings(project.id, update)
    )


@router.post("/settings/test", response_model=LogisticsToolTestResponse)
async def test_query_tool(
    request: LogisticsToolTestRequest,
    db: Session = Depends(get_db),
    project_and_api_key=Depends(get_authenticated_project),
) -> LogisticsToolTestResponse:
    project, _ = project_and_api_key
    result = await _service(db).execute_live_query(project.id, request.tracking_no)
    return LogisticsToolTestResponse(
        success=True,
        message="快递查询工具连接成功",
        preview=result.summary,
    )


@router.get(
    "/visitors/{visitor_id}/shipments",
    response_model=ShipmentListResponse,
)
def list_shipments(
    visitor_id: UUID,
    db: Session = Depends(get_db),
    project_and_api_key=Depends(get_authenticated_project),
) -> ShipmentListResponse:
    project, _ = project_and_api_key
    _ensure_visitor(db, project.id, visitor_id)
    return ShipmentListResponse(
        shipments=_service(db).list_shipments(project.id, visitor_id)
    )


@router.post(
    "/visitors/{visitor_id}/shipments",
    response_model=ShipmentQueryResponse,
    status_code=201,
)
def create_shipment(
    visitor_id: UUID,
    request: ShipmentCreateRequest,
    db: Session = Depends(get_db),
    project_and_api_key=Depends(get_authenticated_project),
) -> ShipmentQueryResponse:
    project, _ = project_and_api_key
    _ensure_visitor(db, project.id, visitor_id)
    shipment = _service(db).create_shipment(
        project_id=project.id,
        visitor_id=visitor_id,
        tracking_no=request.tracking_no,
        source="manual",
        carrier_code=request.carrier_code,
        carrier_name=request.carrier_name,
    )
    return ShipmentQueryResponse(
        shipment=shipment,
        events=(),
        queried_live=False,
        message="物流单已加入顾客档案",
    )


@router.post(
    "/shipments/{shipment_id}/query",
    response_model=ShipmentQueryResponse,
)
async def query_shipment(
    shipment_id: UUID,
    db: Session = Depends(get_db),
    project_and_api_key=Depends(get_authenticated_project),
) -> ShipmentQueryResponse:
    project, _ = project_and_api_key
    shipment, events = await _service(db).query_shipment(
        project.id, shipment_id
    )
    return ShipmentQueryResponse(
        shipment=shipment,
        events=events,
        queried_live=True,
        message="物流轨迹已更新",
    )


@router.get(
    "/shipments/{shipment_id}/events",
    response_model=TrackingEventListResponse,
)
def list_events(
    shipment_id: UUID,
    db: Session = Depends(get_db),
    project_and_api_key=Depends(get_authenticated_project),
) -> TrackingEventListResponse:
    project, _ = project_and_api_key
    return TrackingEventListResponse(
        events=_service(db).list_events(project.id, shipment_id)
    )
