"""Sample creation and retrieval require an administrator's project scope."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from app.api.v1.endpoints import message_analysis
from app.main import app
from app.schemas.shadow_sample import ShadowSampleCreateRequest


def test_sample_routes_are_registered_in_openapi() -> None:
    paths = app.openapi()["paths"]
    collection = "/v1/message-analysis/staff/shadow-sample-batches"
    assert {"post", "get"}.issubset(paths[collection])
    assert "get" in paths[f"{collection}/{{batch_id}}"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/staff/shadow-sample-batches", "POST"),
        ("/staff/shadow-sample-batches", "GET"),
        ("/staff/shadow-sample-batches/{batch_id}", "GET"),
    ],
)
async def test_sample_routes_require_admin(path: str, method: str) -> None:
    route = next(
        item
        for item in message_analysis.router.routes
        if item.path == path and method in item.methods
    )
    admin_check = next(
        dependency.call
        for dependency in route.dependant.dependencies
        if dependency.name == "current_user"
    )
    with pytest.raises(HTTPException) as denied:
        await admin_check(SimpleNamespace(role="agent", username="staff"))
    assert denied.value.status_code == 403


def test_sample_routes_disable_cache_and_scope_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(id=uuid4(), project_id=uuid4(), role="admin")
    request = ShadowSampleCreateRequest(request_id=uuid4(), days=7, sample_size=200)
    batch_id = uuid4()
    service = MagicMock()
    created = SimpleNamespace(id=batch_id)
    service.create_batch.return_value = created
    service.get_batch.return_value = created
    service.list_batches.return_value = SimpleNamespace(items=())
    monkeypatch.setattr(message_analysis, "ShadowSampleService", lambda _db: service)
    response = Response()
    assert (
        message_analysis.create_staff_shadow_sample_batch(
            request=request,
            response=response,
            current_user=admin,
            db=MagicMock(),  # type: ignore[arg-type]
        )
        is created
    )
    assert response.headers["Cache-Control"] == "no-store"
    service.create_batch.assert_called_once_with(admin.project_id, admin.id, request)

    response = Response()
    assert (
        message_analysis.list_staff_shadow_sample_batches(
            response=response,
            limit=20,
            current_user=admin,
            db=MagicMock(),  # type: ignore[arg-type]
        )
        is service.list_batches.return_value
    )
    assert response.headers["Cache-Control"] == "no-store"
    service.list_batches.assert_called_once_with(admin.project_id, limit=20)

    response = Response()
    assert (
        message_analysis.get_staff_shadow_sample_batch(
            batch_id=batch_id,
            response=response,
            current_user=admin,
            db=MagicMock(),  # type: ignore[arg-type]
        )
        is created
    )
    assert response.headers["Cache-Control"] == "no-store"
    service.get_batch.assert_called_once_with(admin.project_id, batch_id)
