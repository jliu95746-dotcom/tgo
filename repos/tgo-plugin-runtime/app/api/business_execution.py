"""Formal protocol capability and query routes require the private business service."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.connection_tests import PrivateTestRoute, require_connection_service
from app.core.database import get_async_db
from app.domain.business_tools.execution_runner import BusinessExecutionRunner
from app.schemas.agent_business_execution import (
    BusinessExecutionCommand,
    BusinessExecutionReceipt,
    BusinessRuntimeCapabilities,
)
from app.schemas.business_query_audit import BusinessMessageAuditQuery, BusinessQueryAuditList
from app.services.business_query_audit_read import list_message_query_audits, list_query_audits

router = APIRouter(
    prefix="/business/execution",
    route_class=PrivateTestRoute,
    dependencies=[Depends(require_connection_service)],
)


@router.get("/capabilities", response_model=BusinessRuntimeCapabilities)
def capabilities() -> BusinessRuntimeCapabilities:
    return BusinessRuntimeCapabilities(
        operations=["order_query", "logistics_query", "customer_profile_query", "membership_query"],
        checked_at=datetime.now(UTC),
    )


def get_runner() -> BusinessExecutionRunner:
    return BusinessExecutionRunner()


@router.get("/audits/{query_id}", response_model=BusinessQueryAuditList)
async def get_query_audits(
    query_id: UUID,
    project_id: UUID,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
) -> BusinessQueryAuditList:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await list_query_audits(db, project_id, query_id)
    except Exception:
        raise HTTPException(503, "业务查询审计暂不可读取") from None


@router.post("/audits/by-message", response_model=BusinessQueryAuditList)
async def get_message_query_audits(
    query: BusinessMessageAuditQuery,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
) -> BusinessQueryAuditList:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await list_message_query_audits(db, query)
    except Exception:
        raise HTTPException(503, "业务查询审计暂不可读取") from None


@router.post("/query", response_model=BusinessExecutionReceipt)
async def query(
    command: BusinessExecutionCommand,
    runner: BusinessExecutionRunner = Depends(get_runner),
) -> BusinessExecutionReceipt:
    try:
        return await runner.run(command)
    except Exception:
        raise HTTPException(503, "正式查询授权、客户身份或结果暂不可核验") from None
