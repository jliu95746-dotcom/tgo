"""Private visitor data requires possession and every current business identity."""

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.core.exceptions import TGOAPIException
from app.models.project import Project
from app.models.platform import Platform
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.models.ai_interaction_run import AIInteractionRun
from app.models.ai_execution_attempt import AIExecutionAttempt
from app.models.visitor_waiting_queue import VisitorWaitingQueue
from app.schemas.visitor_access import VisitorAccessInput
from app.schemas.business_execution_context import BusinessCustomerSessionInput
from app.services.im_access import issue_visitor_im_token
from app.services.im_access import read_visitor_im_access
from app.services.im_access import authenticate_im_actor
from app.services.visitor_access import (
    business_access,
    end_access,
    registration_access,
    require_access,
)
from tests.test_business_customer_identity import setup as setup, claims, sign


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, compiler, **kwargs):
    return "JSON"


@pytest.fixture
def access_fixture(setup):
    identity, db, project, connection, platform, visitor_id, _, _ = setup
    for model in (
        Project, Platform, Visitor, VisitorSession, AIInteractionRun,
        AIExecutionAttempt, VisitorWaitingQueue,
    ):
        model.__table__.create(db.bind)
    db.add(
        Project(
            id=project,
            name="isolated-visitor-access",
            api_key="fixture-only-project-key",
        )
    )
    db.add(
        Platform(
            id=platform,
            project_id=project,
            type="website",
            name="访问隔离",
            is_active=True,
        )
    )
    visitor = Visitor(
        id=visitor_id,
        project_id=project,
        platform_id=platform,
        platform_open_id="public-open-id",
    )
    db.add(visitor)
    db.commit()
    yield identity, db, visitor, connection, setup


def test_public_identifiers_cannot_resume_a_visitor(access_fixture):
    _, db, visitor, _, _ = access_fixture
    secret, _ = registration_access(db, visitor, VisitorAccessInput(), new=True)
    db.commit()
    with pytest.raises(TGOAPIException):
        registration_access(db, visitor, VisitorAccessInput(), new=False)
    assert (
        require_access(
            db, visitor, VisitorAccessInput(visitor_access_token=secret)
        ).references
        == []
    )
    with pytest.raises(TGOAPIException):
        require_access(
            db, visitor, VisitorAccessInput(visitor_access_token=SecretStr("unowned"))
        )


def test_protected_visitor_requires_current_customer_even_with_browser_secret(
    access_fixture,
):
    identity, db, visitor, connection, setup = access_fixture
    secret, _ = registration_access(db, visitor, VisitorAccessInput(), new=True)
    session = identity.exchange(connection, sign(claims(setup)))
    with pytest.raises(TGOAPIException):
        require_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    proof = VisitorAccessInput(
        visitor_access_token=secret,
        customer_sessions=[
            BusinessCustomerSessionInput(
                connection_id=connection, customer_session=session.session_token
            )
        ],
    )
    access = require_access(db, visitor, proof)
    assert (
        access.references[0].binding_id == session.binding_id
        and access.expires_at == session.expires_at
    )
    token = issue_visitor_im_token(visitor, timedelta(days=1), business=access)
    assert (
        require_access(
            db, visitor, VisitorAccessInput(im_token=SecretStr(token))
        ).references
        == access.references
    )
    with pytest.raises(TGOAPIException):
        registration_access(
            db, visitor, VisitorAccessInput(im_token=SecretStr(token)), new=False
        )
    assert (
        registration_access(db, visitor, proof, new=False)[1].references
        == access.references
    )


def test_old_generic_im_token_cannot_prove_protected_customer(access_fixture):
    identity, db, visitor, connection, setup = access_fixture
    registration_access(db, visitor, VisitorAccessInput(), new=True)
    token = issue_visitor_im_token(visitor, timedelta(hours=1))
    identity.exchange(connection, sign(claims(setup)))
    with pytest.raises(TGOAPIException):
        require_access(db, visitor, VisitorAccessInput(im_token=SecretStr(token)))


def test_ending_access_retires_owner_and_closes_live_conversation(access_fixture):
    identity, db, visitor, connection, setup = access_fixture
    secret, _ = registration_access(db, visitor, VisitorAccessInput(), new=True)
    session = identity.exchange(connection, sign(claims(setup)))
    conversation = VisitorSession(
        id=uuid4(), project_id=visitor.project_id, visitor_id=visitor.id, status="open"
    )
    db.add(conversation)
    db.commit()
    end_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    assert visitor.access_retired_at and visitor.access_secret_digest is not None
    revision = visitor.access_revision
    end_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    assert visitor.access_revision == revision
    with pytest.raises(TGOAPIException):
        require_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    db.refresh(conversation)
    assert conversation.status == "closed" and conversation.closed_at
    with pytest.raises(TGOAPIException):
        registration_access(
            db,
            visitor,
            VisitorAccessInput(
                customer_sessions=[
                    BusinessCustomerSessionInput(
                        connection_id=connection, customer_session=session.session_token
                    )
                ]
            ),
            new=False,
        )
    with pytest.raises(TGOAPIException):
        identity.exchange(connection, sign(claims(setup)))


def test_browser_secret_and_customer_sessions_never_serialize(access_fixture):
    identity, db, visitor, connection, setup = access_fixture
    secret, _ = registration_access(db, visitor, VisitorAccessInput(), new=True)
    session = identity.exchange(connection, sign(claims(setup)))
    proof = VisitorAccessInput(
        visitor_access_token=secret,
        customer_sessions=[
            BusinessCustomerSessionInput(
                connection_id=connection, customer_session=session.session_token
            )
        ],
    )
    assert proof.model_dump_json() == "{}"
    assert secret.get_secret_value() not in repr(proof)
    assert session.session_token.get_secret_value() not in repr(proof)
    assert business_access(db, visitor, proof).expires_at == session.expires_at


def test_im_access_expires_with_original_customer_and_rejects_current_revocation(
    access_fixture,
):
    identity, db, visitor, connection, setup = access_fixture
    session = identity.exchange(connection, sign(claims(setup)))
    _, access = registration_access(
        db,
        visitor,
        VisitorAccessInput(
            customer_sessions=[
                BusinessCustomerSessionInput(
                    connection_id=connection, customer_session=session.session_token
                )
            ]
        ),
        new=True,
    )
    token = issue_visitor_im_token(visitor, timedelta(days=30), business=access)
    assert read_visitor_im_access(token).exp <= int(session.expires_at.timestamp())
    assert session.session_token.get_secret_value() not in token
    identity.revoke(visitor.project_id, setup[6], connection, session.binding_id, 1)
    with pytest.raises(TGOAPIException):
        require_access(db, visitor, VisitorAccessInput(im_token=SecretStr(token)))


def test_rotating_browser_access_does_not_allow_the_previous_im_revision(
    access_fixture,
):
    _, db, visitor, _, _ = access_fixture
    secret, _ = registration_access(db, visitor, VisitorAccessInput(), new=True)
    old_token = issue_visitor_im_token(visitor, timedelta(hours=1))
    new_secret, _ = registration_access(
        db, visitor, VisitorAccessInput(im_token=SecretStr(old_token)), new=False
    )
    assert new_secret != secret and visitor.access_revision == 2
    with pytest.raises(TGOAPIException):
        require_access(db, visitor, VisitorAccessInput(im_token=SecretStr(old_token)))
    with pytest.raises(TGOAPIException):
        require_access(db, visitor, VisitorAccessInput(visitor_access_token=secret))
    assert (
        require_access(
            db, visitor, VisitorAccessInput(visitor_access_token=new_secret)
        ).references
        == []
    )


def test_visitor_token_cannot_authorize_a_different_visitor(access_fixture):
    _, db, visitor, _, _ = access_fixture
    secret, _ = registration_access(db, visitor, VisitorAccessInput(), new=True)
    other = Visitor(
        project_id=visitor.project_id,
        platform_id=visitor.platform_id,
        platform_open_id="another-public-id",
    )
    db.add(other)
    db.commit()
    foreign_token = issue_visitor_im_token(other, timedelta(hours=1))
    with pytest.raises(TGOAPIException):
        require_access(
            db, visitor, VisitorAccessInput(im_token=SecretStr(foreign_token))
        )
    with pytest.raises(TGOAPIException):
        require_access(db, other, VisitorAccessInput(visitor_access_token=secret))


@pytest.mark.parametrize("change", ["revoked", "scope_revision", "binding_revision"])
def test_im_checks_current_original_business_binding(access_fixture, change):
    from app.models.business_customer_identity import BusinessCustomerBinding
    from app.models.visitor_business_scope import VisitorBusinessScope
    from datetime import UTC, datetime
    from sqlalchemy import select

    identity, db, visitor, connection, setup = access_fixture
    secret, _ = registration_access(db, visitor, VisitorAccessInput(), new=True)
    session = identity.exchange(connection, sign(claims(setup)))
    proof = VisitorAccessInput(visitor_access_token=secret, customer_sessions=[
        BusinessCustomerSessionInput(connection_id=connection, customer_session=session.session_token)
    ])
    access = require_access(db, visitor, proof)
    token = issue_visitor_im_token(visitor, timedelta(hours=1), business=access)
    assert authenticate_im_actor(db, f"{visitor.id}-vtr", token).id == visitor.id
    binding = db.get(BusinessCustomerBinding, session.binding_id)
    if change == "revoked":
        binding.revoked_at = datetime.now(UTC)
    elif change == "binding_revision":
        binding.revision += 1
    else:
        scope = db.scalar(select(VisitorBusinessScope).where(VisitorBusinessScope.visitor_id == visitor.id))
        scope.revision += 1
    db.commit()
    with pytest.raises(TGOAPIException):
        authenticate_im_actor(db, f"{visitor.id}-vtr", token)
