"""Customer tags remain bound to the original writer, customer and association."""

from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.api.v1.endpoints import tags
from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models import Tag, VisitorTag, VisitorSession, Permission, RolePermission
from app.models.tag import TagCategory
from app.schemas.tag import VisitorTagResponse
from tests.test_im_access import records as records
from tests.test_visitor_profile_access import profile_api as profile_api
from tests.test_staff_visitor_read_access import staff_read_api as staff_read_api
from tests.test_visitor_mutation_access import proof


@pytest.fixture
def tag_api(staff_read_api, monkeypatch):
    app, db, projects, staff, _, visitors, extra, _ = staff_read_api
    app.include_router(tags.router, prefix="/tags")
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    staff[0].role = "user"
    for action in ("create", "delete"):
        permission = Permission(resource="tags", action=action)
        db.add(permission)
        db.flush()
        db.add(RolePermission(role="user", permission_id=permission.id))
    labels = [Tag(name="owned-label", category=TagCategory.VISITOR, project_id=projects[0].id),
              Tag(name="foreign-label", category=TagCategory.VISITOR, project_id=projects[1].id)]
    db.add_all(labels)
    db.commit()
    return app, db, projects, staff, visitors, extra, labels


async def send(app, method, path, headers, body=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, headers=headers, json=body)


def credentials(actor):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=proof(actor)["Authorization"][7:])


@pytest.mark.asyncio
async def test_link_and_both_unlink_routes_preserve_contract_and_no_store(tag_api):
    app, db, _, staff, visitors, _, labels = tag_api
    for by_id in (True, False):
        response = await send(app, "POST", "/tags/visitor-tags", proof(staff[0]),
            {"visitor_id": str(visitors[0].id), "tag_id": labels[0].id})
        assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
        identity = response.json()["id"]
        repeated = await send(app, "POST", "/tags/visitor-tags", proof(staff[0]),
            {"visitor_id": str(visitors[0].id), "tag_id": labels[0].id})
        assert repeated.status_code == 400
        path = f"/tags/visitor-tags/{identity}" if by_id else f"/tags/visitors/{visitors[0].id}/tags/{labels[0].id}"
        removed = await send(app, "DELETE", path, proof(staff[0]))
        assert removed.status_code == 204 and removed.headers["cache-control"] == "no-store"
        assert db.query(VisitorTag).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["foreign-customer", "missing-customer", "deleted-customer", "unowned", "foreign-tag", "missing-tag", "deleted-tag"])
async def test_link_rejects_invalid_or_unowned_target_without_writes(tag_api, target):
    app, db, _, staff, visitors, extra, labels = tag_api
    visitor_id = visitors[0].id
    tag_id = labels[0].id
    if target == "foreign-customer":
        visitor_id = visitors[1].id
    elif target == "missing-customer":
        visitor_id = uuid4()
    elif target == "deleted-customer":
        visitors[0].deleted_at = datetime.utcnow()
    elif target == "unowned":
        db.query(VisitorSession).filter(VisitorSession.visitor_id == visitor_id).one().staff_id = extra.id
    elif target == "foreign-tag":
        tag_id = labels[1].id
    elif target == "missing-tag":
        tag_id = "unknown-label"
    else:
        labels[0].deleted_at = datetime.utcnow()
    db.commit()
    result = await send(app, "POST", "/tags/visitor-tags", proof(staff[0]), {"visitor_id": str(visitor_id), "tag_id": tag_id})
    assert result.status_code == 404 and db.query(VisitorTag).count() == 0


@pytest.mark.parametrize("change", ["revoked", "permission", "owner", "tag", "customer"])
def test_original_scope_rechecks_before_commit(tag_api, change):
    from app.services.visitor_tag_access import begin_tag_change, commit_tag_change

    _, db, _, staff, visitors, extra, labels = tag_api
    scope = begin_tag_change(db, credentials(staff[0]), staff[0], visitors[0].id, labels[0].id)
    if change == "revoked":
        staff[0].token_version += 1
    elif change == "permission":
        permission = db.query(Permission).filter(Permission.resource == "tags", Permission.action == "create").one()
        db.query(RolePermission).filter(RolePermission.permission_id == permission.id).delete()
    elif change == "owner":
        db.query(VisitorSession).filter(VisitorSession.visitor_id == visitors[0].id).one().staff_id = extra.id
    elif change == "tag":
        labels[0].name_zh = "已改名"
        labels[0].updated_at = datetime.utcnow() + timedelta(seconds=1)
    else:
        visitors[0].note = "later-state"
        visitors[0].updated_at = datetime.utcnow() + timedelta(seconds=1)
    db.commit()
    with pytest.raises(TGOAPIException) as failure:
        commit_tag_change(db, scope)
    assert failure.value.status_code == {"revoked": 401, "permission": 403, "owner": 404, "tag": 409, "customer": 409}[change]
    assert db.query(VisitorTag).count() == 0


@pytest.mark.parametrize("by_id", [True, False])
def test_old_unlink_cannot_delete_recreated_association(tag_api, by_id):
    from app.services.visitor_tag_access import begin_tag_change, begin_tag_removal, commit_tag_change

    _, db, _, staff, visitors, _, labels = tag_api
    original = VisitorTag(project_id=staff[0].project_id, visitor_id=visitors[0].id, tag_id=labels[0].id)
    db.add(original)
    db.commit()
    scope = begin_tag_removal(db, credentials(staff[0]), staff[0], original.id) if by_id else begin_tag_change(
        db, credentials(staff[0]), staff[0], visitors[0].id, labels[0].id, remove=True)
    db.delete(original)
    db.flush()
    replacement = VisitorTag(project_id=staff[0].project_id, visitor_id=visitors[0].id, tag_id=labels[0].id)
    db.add(replacement)
    db.commit()
    with pytest.raises(TGOAPIException) as failure:
        commit_tag_change(db, scope)
    assert failure.value.status_code == 409
    assert db.query(VisitorTag).one().id == replacement.id


@pytest.mark.asyncio
@pytest.mark.parametrize("by_id", [True, False])
async def test_unlink_cannot_change_another_staff_customer(tag_api, by_id):
    app, db, _, staff, visitors, extra, labels = tag_api
    association = VisitorTag(project_id=staff[0].project_id, visitor_id=visitors[0].id, tag_id=labels[0].id)
    db.add(association)
    db.query(VisitorSession).filter(VisitorSession.visitor_id == visitors[0].id).one().staff_id = extra.id
    db.commit()
    path = f"/tags/visitor-tags/{association.id}" if by_id else f"/tags/visitors/{visitors[0].id}/tags/{labels[0].id}"
    result = await send(app, "DELETE", path, proof(staff[0]))
    assert result.status_code == 404 and db.query(VisitorTag).count() == 1


@pytest.mark.asyncio
async def test_revoke_at_serialization_refuses_response_without_undoing_commit(tag_api):
    app, db, _, staff, visitors, _, labels = tag_api
    original = VisitorTagResponse.model_validate

    def revoke(value, *args, **kwargs):
        result = original(value, *args, **kwargs)
        staff[0].account_enabled = False
        db.commit()
        return result

    with patch.object(VisitorTagResponse, "model_validate", side_effect=revoke):
        response = await send(app, "POST", "/tags/visitor-tags", proof(staff[0]),
            {"visitor_id": str(visitors[0].id), "tag_id": labels[0].id})
    assert response.status_code == 401 and labels[0].id not in response.text
    assert db.query(VisitorTag).count() == 1
