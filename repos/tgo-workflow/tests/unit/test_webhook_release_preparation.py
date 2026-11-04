"""A fixed webhook graph can be prepared without enabling execution."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.schemas.workflow_release import WorkflowPublishRequest
from app.services import workflow_releases as releases
from app.services.validation_service import ValidationService
from app.services.node_readiness import workflow_capabilities
from tests.unit.test_workflow_webhooks import definition, webhook_database


@pytest.mark.asyncio
async def test_fixed_webhook_preparation_keeps_capability_closed():
    async with webhook_database() as fixture:
        async with fixture.sessions() as db:
            flow = await releases.publish(
                db,
                fixture.workflow_id,
                str(fixture.project_id),
                WorkflowPublishRequest(
                    expected_draft_version=1, expected_publication_revision=1
                ),
                str(fixture.actor_id),
            )
            await db.commit()
            assert flow.published_version == 2
            assert len((await db.scalars(select(WorkflowRelease))).all()) == 2
    capability = next(
        node
        for node in workflow_capabilities().nodes
        if node.type == "webhook"
    )
    assert not capability.available
    assert ValidationService.validate_workflow(definition())


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [("GET", None), ("POST", "/custom")])
async def test_publication_still_rejects_unsupported_webhook_transport(
    method, path
):
    async with webhook_database() as fixture:
        async with fixture.sessions() as db:
            workflow = await db.get(Workflow, fixture.workflow_id)
            graph = definition()
            graph["nodes"][0]["data"].update(method=method, path=path)
            workflow.definition = graph
            await db.commit()
            with pytest.raises(HTTPException) as denied:
                await releases.publish(
                    db,
                    fixture.workflow_id,
                    str(fixture.project_id),
                    WorkflowPublishRequest(
                        expected_draft_version=1,
                        expected_publication_revision=1,
                    ),
                    str(fixture.actor_id),
                )
            assert denied.value.status_code == 400
            assert len((await db.scalars(select(WorkflowRelease))).all()) == 1
