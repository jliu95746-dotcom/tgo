"""Memory clearing retains the tenant and channel scope of chat history."""

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import chat
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.core.security import create_access_token
from app.models import (
    ChannelMemoryClearance,
    Permission,
    Project,
    ProjectRolePermission,
    RolePermission,
    Staff,
    Visitor,
    VisitorSession,
)
from app.services.ai_client import AIServiceClient, ai_client
from app.utils.encoding import (
    build_project_staff_channel_id,
    build_visitor_channel_id,
    get_session_id,
)


@compiles(JSONB, "sqlite")
def _jsonb_for_sqlite(_type, compiler, **kwargs):
    return "JSON"


@dataclass
class MemoryCase:
    app: FastAPI
    db: Session
    staff: Staff
    peer: Staff
    foreign_staff: Staff
    visitor: Visitor
    foreign_visitor: Visitor
    session: VisitorSession
    sequence: AsyncMock
    clear: AsyncMock
    notify: AsyncMock


@pytest.fixture
def memory_case(monkeypatch):
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (
        Project,
        Staff,
        Visitor,
        VisitorSession,
        ChannelMemoryClearance,
        Permission,
        RolePermission,
        ProjectRolePermission,
    ):
        model.__table__.create(engine)
    with Session(engine) as db:
        permission = Permission(resource="chat", action="send")
        db.add(permission)
        db.flush()
        db.add(RolePermission(role="user", permission_id=permission.id))
        projects = [
            Project(name=name, api_key=name) for name in ("one", "two")
        ]
        db.add_all(projects)
        db.flush()
        staff = [
            Staff(
                project_id=project.id,
                username=name,
                role="admin",
                password_hash="synthetic-unused",
            )
            for project, name in (
                (projects[0], "sender"),
                (projects[0], "receiver"),
                (projects[1], "foreign"),
            )
        ]
        visitors = [
            Visitor(
                project_id=project.id,
                platform_id=uuid4(),
                platform_open_id=project.name,
            )
            for project in projects
        ]
        db.add_all(staff + visitors)
        db.flush()
        session = VisitorSession(
            project_id=projects[0].id,
            visitor_id=visitors[0].id,
            staff_id=staff[0].id,
        )
        db.add(session)
        db.commit()
        application = FastAPI()
        application.add_exception_handler(
            TGOAPIException,
            tgo_api_exception_handler,
        )
        application.include_router(chat.router, prefix="/chat")
        application.dependency_overrides[get_db] = lambda: db
        sequence = AsyncMock(return_value=7)
        clear = AsyncMock()
        notify = AsyncMock()
        monkeypatch.setattr(
            chat.wukongim_client,
            "get_channel_max_message_seq",
            sequence,
        )
        monkeypatch.setattr(AIServiceClient, "clear_session_memory", clear)
        monkeypatch.setattr(
            chat.wukongim_client, "send_system_message", notify
        )
        yield MemoryCase(
            application,
            db,
            *staff,
            *visitors,
            session,
            sequence,
            clear,
            notify,
        )
    engine.dispose()


async def _request(case: MemoryCase, channel: str, kind: int):
    token = create_access_token(
        case.staff.username,
        project_id=case.staff.project_id,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=case.app),
        base_url="http://test",
    ) as client:
        return await client.delete(
            "/chat/memory",
            params={"channel_id": channel, "channel_type": kind},
            headers={"Authorization": "Bearer " + token},
        )


def _unchanged(case: MemoryCase) -> None:
    case.clear.assert_not_awaited()
    case.notify.assert_not_awaited()
    assert case.db.query(ChannelMemoryClearance).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "foreign-visitor",
        "foreign-staff",
        "foreign-project",
        "foreign-agent",
        "deleted-visitor",
        "invalid-visitor",
        "unsupported",
    ],
)
async def test_denied_channel_is_rejected_before_read_or_clear(
    memory_case,
    monkeypatch,
    target,
):
    case = memory_case
    channel, kind, expected = (
        build_visitor_channel_id(case.visitor.id),
        251,
        404,
    )
    if target == "foreign-visitor":
        channel = build_visitor_channel_id(case.foreign_visitor.id)
    elif target == "foreign-staff":
        channel, kind = f"{case.foreign_staff.id}-staff", 1
    elif target == "foreign-project":
        channel = build_project_staff_channel_id(case.foreign_staff.project_id)
        kind = 249
    elif target == "foreign-agent":
        channel, kind = f"{uuid4()}-agent", 1
        monkeypatch.setattr(
            ai_client,
            "get_agent",
            AsyncMock(
                side_effect=HTTPException(404, "Agent not found"),
            ),
        )
    elif target == "deleted-visitor":
        case.visitor.deleted_at = datetime.utcnow()
        case.db.commit()
    elif target == "invalid-visitor":
        channel, expected = "not-a-uuid-vtr", 400
    else:
        kind, expected = 999, 400
    response = await _request(case, channel, kind)
    assert response.status_code == expected, response.text
    case.sequence.assert_not_awaited()
    _unchanged(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "user"])
@pytest.mark.parametrize(
    "target", ["visitor", "direct-visitor", "staff", "project", "agent"]
)
async def test_owned_channel_preserves_session_and_clearance(
    memory_case,
    monkeypatch,
    target,
    role,
):
    case = memory_case
    case.staff.role = role
    case.db.commit()
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    channel, kind = build_visitor_channel_id(case.visitor.id), 251
    get_agent = AsyncMock(return_value={"id": str(uuid4())})
    monkeypatch.setattr(ai_client, "get_agent", get_agent)
    if target == "direct-visitor":
        channel, kind = str(case.visitor.id), 1
    elif target == "staff":
        channel, kind = f"{case.peer.id}-staff", 1
    elif target == "project":
        channel, kind = (
            build_project_staff_channel_id(case.staff.project_id),
            249,
        )
    elif target == "agent":
        channel, kind = f"{uuid4()}-agent", 1
    response = await _request(case, channel, kind)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "success": True,
        "message": "Memory cleared successfully",
    }
    uid = f"{case.staff.id}-staff"
    case.clear.assert_awaited_once_with(
        project_id=str(case.staff.project_id),
        session_id=get_session_id(uid, channel, kind),
        user_id=uid,
    )
    rows = case.db.query(ChannelMemoryClearance).all()
    assert len(rows) == 1
    row = rows[0]
    assert (
        row.project_id,
        row.user_id,
        row.channel_id,
        row.channel_type,
        row.cleared_message_seq,
    ) == (
        case.staff.project_id,
        case.staff.id,
        channel,
        kind,
        7,
    )
    assert row.created_at is not None and row.updated_at is not None
    case.notify.assert_awaited_once()
    notification = case.notify.await_args.kwargs
    assert notification["channel_id"] == (uid if kind == 1 else channel)
    assert notification["from_uid"] == (channel if kind == 1 else uid)
    assert notification["channel_type"] == kind
    if target == "agent":
        assert get_agent.await_args.kwargs == {
            "project_id": str(case.staff.project_id),
            "agent_id": channel[:-6],
            "include_tools": False,
            "include_collections": False,
        }


@pytest.mark.asyncio
async def test_repeat_clear_updates_the_same_persisted_boundary(memory_case):
    case = memory_case
    channel = build_visitor_channel_id(case.visitor.id)
    assert (await _request(case, channel, 251)).status_code == 200
    previous = case.db.query(ChannelMemoryClearance).one()
    identity = previous.id
    case.sequence.return_value = 19
    assert (await _request(case, channel, 251)).status_code == 200
    case.db.expire_all()
    current = case.db.query(ChannelMemoryClearance).one()
    assert current.id == identity and current.cleared_message_seq == 19


@pytest.mark.asyncio
async def test_empty_history_does_not_create_a_clearance(memory_case):
    case = memory_case
    case.sequence.return_value = None
    response = await _request(
        case, build_visitor_channel_id(case.visitor.id), 251
    )
    assert response.status_code == 200
    assert case.db.query(ChannelMemoryClearance).count() == 0
    case.clear.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["sequence", "clear"])
async def test_access_revoked_during_remote_wait_stops_the_next_effect(
    memory_case,
    stage,
):
    case = memory_case

    async def revoke(**kwargs):
        case.visitor.deleted_at = datetime.utcnow()
        case.db.commit()
        return 11

    getattr(case, stage).side_effect = revoke
    response = await _request(
        case, build_visitor_channel_id(case.visitor.id), 251
    )
    assert response.status_code == 404, response.text
    case.notify.assert_not_awaited()
    if stage == "sequence":
        _unchanged(case)
    else:
        case.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_notification_error_does_not_expose_remote_details(
    memory_case,
    caplog,
    capsys,
):
    case = memory_case
    secret = "synthetic-private-notification-detail"
    case.notify.side_effect = RuntimeError(secret)
    response = await _request(
        case, build_visitor_channel_id(case.visitor.id), 251
    )
    assert response.status_code == 200
    assert secret not in response.text + caplog.text
    captured = capsys.readouterr()
    assert "existing---->" not in captured.out
    assert "max_seq---->" not in captured.out
    assert "Failed to send memory cleared system message" in caplog.text


@pytest.mark.asyncio
async def test_transferred_staff_cannot_clear_the_previous_customer(
    memory_case, monkeypatch
):
    case = memory_case
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    case.staff.role = "user"
    case.session.staff_id = case.peer.id
    case.db.commit()
    response = await _request(
        case,
        build_visitor_channel_id(case.visitor.id),
        251,
    )
    assert response.status_code == 404, response.text
    case.sequence.assert_not_awaited()
    _unchanged(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["sequence", "clear"])
@pytest.mark.parametrize(
    "change",
    [
        "disabled",
        "token-version",
        "role",
        "project",
        "deleted",
        "project-deleted",
    ],
)
async def test_original_staff_authority_is_rechecked_after_remote_wait(
    memory_case,
    stage,
    change,
):
    case = memory_case

    async def revoke(**kwargs):
        if change == "disabled":
            case.staff.account_enabled = False
        elif change == "token-version":
            case.staff.token_version += 1
        elif change == "role":
            case.staff.role = "user"
        elif change == "project":
            case.staff.project_id = case.foreign_staff.project_id
        elif change == "deleted":
            case.staff.deleted_at = datetime.utcnow()
        else:
            project = case.db.get(Project, case.staff.project_id)
            project.deleted_at = datetime.utcnow()
        case.db.commit()
        return 11

    getattr(case, stage).side_effect = revoke
    response = await _request(
        case,
        build_visitor_channel_id(case.visitor.id),
        251,
    )
    assert response.status_code == 401, response.text
    case.notify.assert_not_awaited()
    if stage == "sequence":
        _unchanged(case)
    else:
        case.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_revocation_during_agent_scope_query_stops_im_access(
    memory_case,
    monkeypatch,
):
    case = memory_case

    async def revoked(**kwargs):
        case.staff.account_enabled = False
        case.db.commit()
        return {"id": "synthetic-agent"}

    monkeypatch.setattr(ai_client, "get_agent", AsyncMock(side_effect=revoked))
    response = await _request(case, f"{uuid4()}-agent", 1)
    assert response.status_code == 401, response.text
    case.sequence.assert_not_awaited()
    _unchanged(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["sequence", "clear"])
async def test_permission_removal_during_remote_wait_stops_the_next_effect(
    memory_case,
    monkeypatch,
    stage,
):
    case = memory_case
    case.staff.role = "user"
    case.db.commit()
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)

    async def revoke(**kwargs):
        case.db.query(RolePermission).delete()
        case.db.commit()
        return 11

    getattr(case, stage).side_effect = revoke
    response = await _request(
        case,
        build_visitor_channel_id(case.visitor.id),
        251,
    )
    assert response.status_code == 403, response.text
    case.notify.assert_not_awaited()
    if stage == "sequence":
        _unchanged(case)
    else:
        case.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_staff_without_send_permission_cannot_start(memory_case):
    case = memory_case
    case.staff.role = "user"
    case.db.query(RolePermission).delete()
    case.db.commit()
    response = await _request(
        case,
        build_visitor_channel_id(case.visitor.id),
        251,
    )
    assert response.status_code == 403, response.text
    case.sequence.assert_not_awaited()
    _unchanged(case)
