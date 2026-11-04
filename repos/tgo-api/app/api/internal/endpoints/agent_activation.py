"""Private template authority for AI activation checks; never a public API."""

from secrets import compare_digest

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.agent_activation import (
    AgentActivationRequirements,
    AgentActivationRequirementsQuery,
)
from app.services.agent_activation_requirements import current_requirements
from app.schemas.agent_activation_models import AgentActivationModel, AgentActivationModelQuery
from app.services.agent_activation_models import current_model
from app.schemas.agent_configuration_actor import AgentConfigurationActor, AgentConfigurationActorQuery
from app.services.agent_configuration_actor import current_configuration_actor
from app.schemas.agent_business_connections import BusinessConnectionOwner, ConnectionOwnerQuery
from app.services.agent_business_connection_owner import current_business_connection
from app.schemas.agent_business_reviews import BusinessTestEvidenceQuery, BusinessTestEvidence
from app.services.agent_business_evidence import current_evidence
from app.schemas.agent_business_execution import BusinessRuntimeQuery, BusinessRuntimeReadiness
from app.services.business_runtime_readiness import current_business_runtime


def require_activation_service(x_internal_api_key: str | None = Header(None)) -> None:
    if (
        not settings.SECRET_KEY
        or not x_internal_api_key
        or not compare_digest(
            x_internal_api_key.encode("utf-8"),
            settings.SECRET_KEY.encode("utf-8"),
        )
    ):
        raise HTTPException(403, "内部服务身份无效")


router = APIRouter(dependencies=[Depends(require_activation_service)])


@router.post("/business-runtime", response_model=BusinessRuntimeReadiness)
async def business_runtime(query: BusinessRuntimeQuery, db: Session = Depends(get_db)) -> BusinessRuntimeReadiness:
    return await current_business_runtime(db, query)


@router.post("/business-test-evidence", response_model=BusinessTestEvidence)
def business_test_evidence(query: BusinessTestEvidenceQuery, db: Session = Depends(get_db)) -> BusinessTestEvidence:
    return current_evidence(db,query)


@router.post("/business-connection", response_model=BusinessConnectionOwner)
def business_connection(query: ConnectionOwnerQuery, db: Session = Depends(get_db)) -> BusinessConnectionOwner:
    return current_business_connection(db, query)


@router.post("/actor", response_model=AgentConfigurationActor)
def configuration_actor(query: AgentConfigurationActorQuery, db: Session = Depends(get_db)) -> AgentConfigurationActor:
    return current_configuration_actor(db, query)


@router.post("/requirements", response_model=AgentActivationRequirements)
def requirements(
    query: AgentActivationRequirementsQuery,
    db: Session = Depends(get_db),
) -> AgentActivationRequirements:
    return current_requirements(db, query)


@router.post("/model", response_model=AgentActivationModel)
def model_configuration(
    query: AgentActivationModelQuery,
    db: Session = Depends(get_db),
) -> AgentActivationModel:
    return current_model(db, query)
