"""Business owner attestation uses fresh private metadata without credentials."""
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.exceptions import ExternalServiceError
from app.schemas.agent_business_connections import BusinessConnectionOwner, ConnectionOwnerQuery
from app.services.api_service import APIServiceClient


@pytest.mark.asyncio
@pytest.mark.parametrize("fault",[None,"scope","foreign","enable","old","missing","secret","operations_missing","nested_missing","expired_proof","nested_enable"])
async def test_business_owner_identity_scope_and_freshness(fault):
    query=ConnectionOwnerQuery(project_id=uuid4(),requested_by=uuid4(),connection_id=uuid4())
    body=BusinessConnectionOwner(**query.model_dump(),name="订单",mode="http",enabled=False,
        operations=["order_query"],revision=1,configuration_revision=1,authorization_revision=1,
        resource_revision="a"*64,test_status="not_tested",checked_at=datetime.now(UTC),
        configuration_tests=[{"operation":"order_query","report_id":None,"status":"not_tested","valid_until":None,"issues":[]}]).model_dump(mode="json")
    if fault=="scope":body["scope"]="employee_execution_check"
    if fault=="foreign":body["connection_id"]=str(uuid4())
    if fault=="enable":body["can_enable"]=True
    if fault=="old":body["checked_at"]="2020-01-01T00:00:00Z"
    if fault=="missing":del body["can_enable"]
    if fault=="secret":body["credentials"]={"token":"fixture"}
    if fault=="operations_missing":body["configuration_tests"]=[]
    if fault=="nested_missing":del body["configuration_tests"][0]["can_enable"]
    if fault=="expired_proof":body["configuration_tests"][0].update(status="current",report_id=str(uuid4()),valid_until="2020-01-01T00:00:00Z")
    if fault=="nested_enable":body["configuration_tests"][0]["can_enable"]=True
    def reply(request):
        assert request.url.path=="/internal/ai/activation/business-connection"
        return httpx.Response(200,json=body)
    client=APIServiceClient();client._http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply))
    try:
        if fault:
            with pytest.raises(ExternalServiceError):await client.get_agent_business_connection(query)
        else:
            result=await client.get_agent_business_connection(query)
            assert result.connection_id==query.connection_id and not result.can_enable
    finally:await client.aclose()
