"""Administrators configure only their company's business connections."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.company_configuration_access import require_configuration_write
from app.core.database import get_db
from app.core.security import require_admin
from app.models.staff import Staff
from app.schemas.business_connections import (
    BusinessConnectionCreate,
    BusinessConnectionUpdate,
    BusinessConnectionResponse,
    BusinessConnectionRevisionRequest,
)
from app.services import business_connections
from app.schemas.business_connection_tests import (
    BusinessConnectionTestRequest,
    BusinessConnectionTestReport,
)
from app.services.business_connection_tests import BusinessConnectionTestService
from app.services.request_execution import run_until_disconnect

router = APIRouter()


@router.get("/requests/{request_id}", response_model=BusinessConnectionResponse)
def recover_connection_creation(
    request_id: UUID,
    actor: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> BusinessConnectionResponse:
    return business_connections.creation_result(db, actor.project_id, request_id)


def get_connection_test_service(
    db: Session = Depends(get_db),
) -> BusinessConnectionTestService:
    return BusinessConnectionTestService(db)


@router.post(
    "/{connection_id}/tests",
    response_model=BusinessConnectionTestReport,
    dependencies=[Depends(require_configuration_write)],
)
async def submit_connection_test(
    connection_id: UUID,
    payload: BusinessConnectionTestRequest,
    request: Request,
    actor: Staff = Depends(require_admin()),
    service: BusinessConnectionTestService = Depends(get_connection_test_service),
) -> BusinessConnectionTestReport:
    return await run_until_disconnect(
        lambda: service.run(actor.project_id, actor.id, connection_id, payload), request
    )


@router.get("/{connection_id}/tests", response_model=list[BusinessConnectionTestReport])
def list_connection_tests(
    connection_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    actor: Staff = Depends(require_admin()),
    service: BusinessConnectionTestService = Depends(get_connection_test_service),
) -> list[BusinessConnectionTestReport]:
    return service.list(actor.project_id, actor.id, connection_id, limit)


@router.get(
    "/{connection_id}/tests/{request_id}", response_model=BusinessConnectionTestReport
)
def get_connection_test(
    connection_id: UUID,
    request_id: UUID,
    actor: Staff = Depends(require_admin()),
    service: BusinessConnectionTestService = Depends(get_connection_test_service),
) -> BusinessConnectionTestReport:
    return service.get(actor.project_id, actor.id, connection_id, request_id)


@router.post(
    "/{connection_id}/tests/{request_id}/cancel",
    response_model=BusinessConnectionTestReport,
)
def cancel_connection_test(
    connection_id: UUID,
    request_id: UUID,
    actor: Staff = Depends(require_admin()),
    service: BusinessConnectionTestService = Depends(get_connection_test_service),
) -> BusinessConnectionTestReport:
    return service.cancel(actor.project_id, actor.id, connection_id, request_id)


@router.get("", response_model=list[BusinessConnectionResponse])
def list_connections(
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    actor: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> list[BusinessConnectionResponse]:
    return business_connections.list_connections(
        db, actor.project_id, limit=limit, offset=offset
    )


@router.post(
    "",
    response_model=BusinessConnectionResponse,
    status_code=201,
    dependencies=[Depends(require_configuration_write)],
)
def create_connection(
    payload: BusinessConnectionCreate,
    actor: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> BusinessConnectionResponse:
    return business_connections.create(db, actor.project_id, actor.id, payload)


@router.get("/{connection_id}", response_model=BusinessConnectionResponse)
def get_connection(
    connection_id: UUID,
    actor: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> BusinessConnectionResponse:
    return business_connections.get(db, actor.project_id, connection_id)


@router.patch(
    "/{connection_id}",
    response_model=BusinessConnectionResponse,
    dependencies=[Depends(require_configuration_write)],
)
def update_connection(
    connection_id: UUID,
    payload: BusinessConnectionUpdate,
    actor: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> BusinessConnectionResponse:
    return business_connections.update(
        db, actor.project_id, actor.id, connection_id, payload
    )


@router.post("/{connection_id}/disable", response_model=BusinessConnectionResponse)
def disable_connection(
    connection_id: UUID,
    payload: BusinessConnectionRevisionRequest,
    actor: Staff = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> BusinessConnectionResponse:
    # Stopping a connection remains available without purchasing new service.
    return business_connections.update(
        db,
        actor.project_id,
        actor.id,
        connection_id,
        BusinessConnectionUpdate(
            expected_revision=payload.expected_revision, enabled=False
        ),
    )
