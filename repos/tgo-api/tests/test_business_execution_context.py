"""Signed customer sessions are bound to one live conversation/request attempt."""

from datetime import UTC, datetime
from uuid import uuid4
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.core.exceptions import TGOAPIException
from app.models.ai_interaction_run import AIInteractionRun
from app.models.visitor_session import VisitorSession
from app.models.business_customer_identity import BusinessCustomerBinding
from app.models.business_connection import BusinessConnection
from app.schemas.business_execution_context import (
    BusinessCustomerSessionInput,
    BusinessContextCheck,
)
from app.services.business_execution_context import BusinessExecutionContextService
from app.services.knowledge_channel import resolve_platform_knowledge_channel
from app.services.chat_service import resolve_service_mode
from app.utils.encoding import build_visitor_channel_id
from tests.test_business_customer_identity import setup as setup, claims, sign


@pytest.fixture
def context_fixture(setup):
    identity, db, project, connection, platform, visitor, _, _ = setup
    for model in (VisitorSession, AIInteractionRun):
        model.__table__.create(db.bind)
    current = identity.visitor_reader(project, visitor)
    current.ai_disabled, current.service_mode = False, "auto"
    conversation = VisitorSession(
        id=uuid4(),
        project_id=project,
        visitor_id=visitor,
        platform_id=platform,
        staff_id=uuid4(),
        status="open",
    )
    run = AIInteractionRun(
        id=uuid4(),
        project_id=project,
        platform_id=platform,
        visitor_id=visitor,
        channel_id=build_visitor_channel_id(visitor),
        channel_type=251,
        source_message_id="source-is-not-a-conversation",
        request_fingerprint="a" * 64,
        response_client_msg_no="ai_" + uuid4().hex,
        status="running",
    )
    db.add_all([conversation, run])
    db.commit()
    session = identity.exchange(connection, sign(claims(setup)))
    service = BusinessExecutionContextService(
        db,
        identity=identity,
        platform_channel_reader=lambda p, x: (
            resolve_platform_knowledge_channel("website")
            if (p, x) == (project, platform)
            else None
        ),
        service_mode_reader=lambda p, x, v: resolve_service_mode(
            SimpleNamespace(ai_mode="auto"), v
        ),
    )
    auth = service.issue(
        run.id,
        project,
        visitor,
        [
            BusinessCustomerSessionInput(
                connection_id=connection, customer_session=session.session_token
            )
        ],
    )
    check = BusinessContextCheck(
        project_id=project,
        visitor_id=visitor,
        channel="web",
        request_id=run.id,
        request_fingerprint=run.request_fingerprint,
        context_token=auth.context_token,
    )
    yield service, db, run, conversation, session, auth, check, current


def test_real_customer_is_bound_to_live_conversation_and_original_request(
    context_fixture,
):
    service, _, run, conversation, session, auth, check, _ = context_fixture
    principal = service.verify(check)
    assert (
        principal.request_id == run.id
        and principal.visitor_session_id == conversation.id
    )
    assert principal.source_message_id == run.source_message_id
    assert principal.source_message_id != str(principal.visitor_session_id)
    assert principal.bindings[0].binding_id == session.binding_id
    assert (
        principal.context_digest == auth.context_digest
        and principal.can_enable is False
    )
    assert "merchant-member-private-001" not in principal.model_dump_json()
    assert session.session_token.get_secret_value() not in auth.model_dump_json()
    assert auth.context_token.get_secret_value() not in repr(auth)
    assert auth.expires_at <= session.expires_at


@pytest.mark.parametrize(
    "fault",
    [
        "project",
        "visitor",
        "channel",
        "request",
        "fingerprint",
        "token",
        "closed_conversation",
        "different_attempt",
        "finished_request",
        "revoked_customer",
        "manual",
        "changed_connection",
    ],
)
def test_changed_or_forged_context_is_rejected_before_any_business_query(
    context_fixture, fault
):
    service, db, run, conversation, session, auth, check, visitor = context_fixture
    if fault in {"project", "visitor", "request"}:
        check = check.model_copy(update={fault + "_id": uuid4()})
    elif fault == "channel":
        check = check.model_copy(update={"channel": "wecom_kf"})
    elif fault == "fingerprint":
        check = check.model_copy(update={"request_fingerprint": "b" * 64})
    elif fault == "token":
        check = check.model_copy(
            update={"context_token": SecretStr("self-declared-private-context")}
        )
    elif fault == "closed_conversation":
        conversation.status, conversation.closed_at = "closed", datetime.now(UTC)
    elif fault == "different_attempt":
        run.response_client_msg_no = "ai_" + uuid4().hex
    elif fault == "finished_request":
        run.status = "completed"
    elif fault == "revoked_customer":
        db.get(BusinessCustomerBinding, session.binding_id).revoked_at = datetime.now(
            UTC
        )
    elif fault == "changed_connection":
        db.get(BusinessConnection, session.connection_id).configuration_revision += 1
    else:
        visitor.service_mode = "manual"
    db.commit()
    with pytest.raises(TGOAPIException) as error:
        service.verify(check)
    assert auth.context_token.get_secret_value() not in str(error.value)


def test_claim_without_signed_customer_or_on_a_custom_channel_cannot_issue(
    context_fixture,
):
    service, db, run, _, session, _, _, _ = context_fixture
    with pytest.raises(TGOAPIException):
        service.issue(run.id, run.project_id, run.visitor_id, [])
    run.channel_id = "another-channel"
    db.commit()
    with pytest.raises(TGOAPIException):
        service.issue(
            run.id,
            run.project_id,
            run.visitor_id,
            [
                BusinessCustomerSessionInput(
                    connection_id=session.connection_id,
                    customer_session=session.session_token,
                )
            ],
        )


def test_legacy_auto_flag_keeps_existing_service_mode_semantics(context_fixture):
    service, _, _, _, _, _, check, visitor = context_fixture
    visitor.service_mode = None
    assert service.verify(check).can_enable is False
    visitor.ai_disabled = None
    assert service.verify(check).can_enable is False


@pytest.mark.asyncio
async def test_private_context_route_requires_service_identity_and_hides_credentials(
    context_fixture, monkeypatch
):
    import httpx
    from fastapi import FastAPI
    from app.api.internal.endpoints import business_execution_context as endpoint
    from app.core.database import get_db
    from app.core.config import settings

    service, db, _, _, _, auth, check, _ = context_fixture
    monkeypatch.setattr(settings, "SECRET_KEY", "fixture-private-context-service-key")
    monkeypatch.setattr(endpoint, "BusinessExecutionContextService", lambda _: service)
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/internal")
    app.dependency_overrides[get_db] = lambda: db
    body = {
        **check.model_dump(mode="json"),
        "context_token": check.context_token.get_secret_value(),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        path = "/internal/ai/business-execution/context-check"
        denied = await client.post(path, json=body)
        assert (
            denied.status_code == 403
            and auth.context_token.get_secret_value() not in denied.text
        )
        allowed = await client.post(
            path, json=body, headers={"X-Internal-API-Key": settings.SECRET_KEY}
        )
        assert allowed.status_code == 200 and allowed.json()["can_enable"] is False
        assert auth.context_token.get_secret_value() not in allowed.text
        invalid = await client.post(
            path,
            json={**body, "visitor_id": "invalid-private-visitor"},
            headers={"X-Internal-API-Key": settings.SECRET_KEY},
        )
        assert (
            invalid.status_code == 422
            and auth.context_token.get_secret_value() not in invalid.text
        )
