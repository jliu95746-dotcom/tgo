"""Question configuration never enables or switches employees."""
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.api.company_configuration_access import require_configuration_write
from app.core.security import require_admin
from app.models.staff import Staff
from app.schemas.agent_knowledge_cases import (
    KnowledgeCatalog,
    KnowledgeCatalogWrite,
)
from app.services import agent_knowledge_cases


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    dependencies=[Depends(require_configuration_write), Depends(no_store)]
)


@router.get(
    "/{agent_id}/knowledge-test-catalog", response_model=KnowledgeCatalog
)
async def read_catalog(
    agent_id: UUID, actor: Staff = Depends(require_admin())
) -> KnowledgeCatalog:
    return await agent_knowledge_cases.read(
        actor.project_id, actor.id, agent_id
    )


@router.post(
    "/{agent_id}/knowledge-test-catalog", response_model=KnowledgeCatalog
)
async def save_catalog(
    agent_id: UUID,
    payload: KnowledgeCatalogWrite,
    actor: Staff = Depends(require_admin()),
) -> KnowledgeCatalog:
    return await agent_knowledge_cases.save(
        actor.project_id, actor.id, agent_id, payload
    )
