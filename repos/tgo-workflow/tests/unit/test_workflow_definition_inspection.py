"""Metadata readers keep their contracts and reject malformed saved graphs."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api import workflows
from app.database import get_db
from app.services import workflow_releases


MARKER = "synthetic-private-definition-marker"


def sample_definition():
    def node(identifier, kind, **data):
        return {
            "id": identifier,
            "type": kind,
            "data": {"reference_key": identifier, "label": identifier, **data},
        }

    return {
        "nodes": [
            node(
                "input",
                "input",
                input_variables=[
                    {
                        "name": "query",
                        "type": "string",
                        "required": True,
                        "description": "Synthetic question",
                        "default": "",
                    },
                    {"name": "count", "type": "number", "default": 0},
                    {"name": "enabled", "type": "boolean", "default": False},
                ],
            ),
            *[
                node(kind, kind)
                for kind in (
                    "webhook",
                    "timer",
                    "event",
                    "llm",
                    "agent",
                    "tool",
                    "api",
                    "classifier",
                    "answer",
                    "condition",
                    "future-node",
                )
            ],
        ],
        "edges": [],
        "private_configuration": MARKER,
    }


def saved_workflow(definition, *, status="draft"):
    return SimpleNamespace(
        id=str(uuid4()),
        project_id=str(uuid4()),
        name="Synthetic workflow",
        description=None,
        tags=[],
        status=status,
        version=2,
        published_version=1 if status == "active" else None,
        publication_revision=1,
        updated_at=datetime.now(UTC),
        definition=definition,
    )


def app_for(monkeypatch, workflow):
    app = FastAPI()
    app.include_router(workflows.router, prefix="/v1/workflows")
    app.dependency_overrides[get_db] = lambda: object()
    monkeypatch.setattr(
        workflows.WorkflowService,
        "get_by_id",
        AsyncMock(return_value=workflow),
    )
    monkeypatch.setattr(
        workflows.WorkflowService,
        "get_by_ids",
        AsyncMock(return_value=[workflow]),
    )
    return app


@pytest.mark.asyncio
async def test_metadata_contract_preserves_outputs_and_false_zero_defaults(
    monkeypatch,
):
    definition = sample_definition()
    original = deepcopy(definition)
    row = saved_workflow(definition)
    app = app_for(monkeypatch, row)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        scope = {"project_id": row.project_id}
        response = await client.get(
            f"/v1/workflows/{row.id}/variables", params=scope
        )
        assert response.status_code == 200
        variables = response.json()["variables"]
        assert [item["node_type"] for item in variables] == [
            "input",
            "webhook",
            "timer",
            "event",
            "llm",
            "agent",
            "tool",
            "api",
            "classifier",
        ]
        assert variables[0]["outputs"] == [
            {
                "name": "query",
                "type": "string",
                "description": "Synthetic question",
            },
            {"name": "count", "type": "number", "description": None},
            {"name": "enabled", "type": "boolean", "description": None},
        ]
        assert variables[1]["outputs"] == [
            {
                "name": "body",
                "type": "object",
                "description": "Webhook request body",
            },
            {
                "name": "params",
                "type": "object",
                "description": "Webhook query parameters",
            },
            {
                "name": "headers",
                "type": "object",
                "description": "Webhook request headers",
            },
        ]
        assert variables[-1]["outputs"] == [
            {
                "name": "category_id",
                "type": "string",
                "description": "Matched category ID",
            },
            {
                "name": "category_name",
                "type": "string",
                "description": "Matched category name",
            },
        ]
        response = await client.get(
            "/v1/workflows/batch", params={**scope, "workflow_ids": row.id}
        )
        assert response.status_code == 200
        params = response.json()[0]["input_parameters"]
        assert [item["default"] for item in params] == ["", 0, False]
        assert [item["required"] for item in params] == [True, False, False]
        assert MARKER not in response.text
    assert definition == original


MALFORMED = [
    {"nodes": None},
    {"nodes": 7},
    {"nodes": MARKER},
    {"nodes": {}},
    {"nodes": [None]},
    {"nodes": [MARKER]},
    {"nodes": [[]]},
    {"nodes": [{"type": "input", "data": None}]},
    {"nodes": [{"type": "input", "data": {"input_variables": MARKER}}]},
    {"nodes": [{"type": "input", "data": {"input_variables": [None]}}]},
    {
        "nodes": [
            {
                "type": "input",
                "data": {
                    "input_variables": [
                        {"name": MARKER, "type": None},
                    ]
                },
            }
        ]
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", MALFORMED)
@pytest.mark.parametrize("endpoint", ["batch", "variables"])
async def test_invalid_saved_metadata_returns_controlled_conflict(
    monkeypatch, definition, endpoint
):
    row = saved_workflow(definition)
    app = app_for(monkeypatch, row)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        path = (
            "/v1/workflows/batch"
            if endpoint == "batch"
            else f"/v1/workflows/{row.id}/variables"
        )
        response = await client.get(
            path,
            params={
                "project_id": row.project_id,
                "workflow_ids": row.id,
            },
        )
        assert response.status_code == 409
        assert MARKER not in response.text
        assert response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", MALFORMED)
async def test_invalid_release_metadata_preserves_frozen_definition(
    monkeypatch, definition
):
    row = saved_workflow(definition, status="active")
    original = deepcopy(definition)
    published = workflow_releases.PublishedDefinition(
        definition, 1, str(uuid4())
    )
    monkeypatch.setattr(
        workflow_releases,
        "production_definition",
        AsyncMock(return_value=published),
    )
    with pytest.raises(HTTPException) as error:
        await workflow_releases.integration_release(
            object(), row.id, row.project_id, 1
        )
    assert error.value.status_code == 409
    assert MARKER not in str(error.value.detail)
    assert definition == original


@pytest.mark.asyncio
async def test_empty_draft_and_input_batch_defaults_remain_supported(
    monkeypatch,
):
    row = saved_workflow({})
    app_for(monkeypatch, row)
    assert (
        await workflows.get_workflow_variables(
            row.id, row.project_id, object()
        )
    ).variables == []
    row.definition = {
        "nodes": [{"type": "input", "data": {"input_variables": [{}]}}]
    }
    result = await workflows.get_workflows_batch(
        [row.id], row.project_id, object()
    )
    assert result[0].input_parameters[0].model_dump() == {
        "name": "",
        "type": "string",
        "description": None,
        "required": False,
        "default": None,
    }


@pytest.mark.asyncio
async def test_active_batch_uses_fixed_release_and_variables_use_current_draft(
    monkeypatch,
):
    fixed = sample_definition()
    draft = deepcopy(fixed)
    draft["nodes"][0]["data"]["input_variables"][0]["name"] = "draft_query"
    row = saved_workflow(draft, status="active")
    app_for(monkeypatch, row)
    published = workflow_releases.PublishedDefinition(fixed, 1, str(uuid4()))
    read = AsyncMock(return_value=published)
    monkeypatch.setattr(workflow_releases, "production_definition", read)
    batch = await workflows.get_workflows_batch(
        [row.id], row.project_id, object()
    )
    variables = await workflows.get_workflow_variables(
        row.id, row.project_id, object()
    )
    assert batch[0].input_parameters[0].name == "query"
    assert variables.variables[0].outputs[0].name == "draft_query"
    read.assert_awaited_once()
    assert fixed["nodes"][0]["data"]["input_variables"][0]["name"] == "query"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["id", "label", "reference_key"])
async def test_invalid_variable_identity_does_not_return_partial_metadata(
    monkeypatch, field
):
    definition = sample_definition()
    node = definition["nodes"][1]
    (node if field == "id" else node["data"])[field] = None
    row = saved_workflow(definition)
    app_for(monkeypatch, row)
    with pytest.raises(HTTPException) as error:
        await workflows.get_workflow_variables(
            row.id, row.project_id, object()
        )
    assert error.value.status_code == 409


@pytest.mark.parametrize(
    "identifier,digest,expected",
    [
        (None, None, None),
        (None, "a" * 64, 422),
        ("release-a", None, 422),
        ("release-b", "a" * 64, 409),
        ("release-a", "a" * 64, 409),
    ],
)
def test_binding_requires_complete_matching_release_pair(
    identifier, digest, expected
):
    published = workflow_releases.PublishedDefinition({}, 1, "release-a")
    if expected is None:
        workflow_releases.verify_execution_binding(
            published, identifier, digest
        )
    else:
        with pytest.raises(HTTPException) as error:
            workflow_releases.verify_execution_binding(
                published, identifier, digest
            )
        assert error.value.status_code == expected
