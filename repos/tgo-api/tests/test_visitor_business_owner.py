"""A signed assertion cannot repurpose another customer's existing visitor."""

from uuid import uuid4
from datetime import UTC, datetime

import pytest

from app.core.exceptions import ConflictError
from app.models.visitor_business_scope import VisitorBusinessScope
from app.services.visitor_business_scope import (
    ensure_owner,
    MAX_VISITOR_BUSINESS_SCOPES,
)
from tests.test_business_customer_identity import setup as setup, claims, sign


def test_same_visitor_cannot_be_bound_to_another_signed_customer(setup):
    service, _, _, connection, _, _, _, _ = setup
    first = service.exchange(connection, sign(claims(setup)))
    other = claims(setup)
    other.update(nonce=str(uuid4()), customer_id="another-merchant-customer")
    with pytest.raises(ConflictError) as changed:
        service.exchange(connection, sign(other))
    assert changed.value.details["reason"] == "visitor_owner_changed"
    assert (
        service.resolve(
            first.project_id, connection, first.visitor_id, first.session_token
        ).binding_id
        == first.binding_id
    )


def test_same_customer_can_acquire_another_short_session_without_changing_owner(setup):
    service, _, _, connection, _, _, _, _ = setup
    first = service.exchange(connection, sign(claims(setup)))
    again = claims(setup)
    again["nonce"] = str(uuid4())
    second = service.exchange(connection, sign(again))
    assert second.binding_id != first.binding_id
    assert (
        service.resolve(
            second.project_id, connection, second.visitor_id, second.session_token
        ).customer_id
        == service.resolve(
            first.project_id, connection, first.visitor_id, first.session_token
        ).customer_id
    )


def test_expired_or_revoked_binding_does_not_release_owner(setup):
    service, db, project, connection, _, visitor, actor, _ = setup
    first = service.exchange(connection, sign(claims(setup)))
    service.revoke(project, actor, connection, first.binding_id, 1)
    other = claims(setup, nonce=str(uuid4()), customer_id="different-customer")
    with pytest.raises(ConflictError):
        service.exchange(connection, sign(other))
    assert (
        ensure_owner(
            db, project, visitor, connection, "merchant-member-private-001"
        ).retired_at
        is None
    )


def test_retired_owner_cannot_be_resumed_even_with_a_fresh_signature(setup):
    service, db, project, connection, _, visitor, _, _ = setup
    first = service.exchange(connection, sign(claims(setup)))
    scope = ensure_owner(
        db, project, visitor, connection, "merchant-member-private-001"
    )
    scope.retired_at = datetime.now(UTC)
    db.commit()
    with pytest.raises(ConflictError):
        service.resolve(project, connection, visitor, first.session_token)
    with pytest.raises(ConflictError):
        service.exchange(connection, sign(claims(setup, nonce=str(uuid4()))))


def test_legacy_same_customer_can_verify_owner_without_silently_trusting_history(setup):
    service, db, project, connection, _, visitor, _, _ = setup
    first = service.exchange(connection, sign(claims(setup)))
    scope = ensure_owner(
        db, project, visitor, connection, "merchant-member-private-001"
    )
    db.delete(scope)
    db.commit()
    with pytest.raises(ConflictError):
        service.resolve(project, connection, visitor, first.session_token)
    service.exchange(connection, sign(claims(setup, nonce=str(uuid4()))))
    assert (
        service.resolve(project, connection, visitor, first.session_token).binding_id
        == first.binding_id
    )
    assert len(list(db.query(VisitorBusinessScope))) == 1


def test_mixed_legacy_customer_history_cannot_be_assigned_an_owner(setup):
    service, db, project, connection, _, visitor, _, _ = setup
    service.exchange(connection, sign(claims(setup)))
    db.delete(
        ensure_owner(db, project, visitor, connection, "merchant-member-private-001")
    )
    db.commit()
    with pytest.raises(ConflictError):
        service.exchange(
            connection,
            sign(claims(setup, nonce=str(uuid4()), customer_id="different-customer")),
        )
    assert db.query(VisitorBusinessScope).count() == 0


def test_customer_scope_limit_matches_the_bounded_access_proof(setup):
    service, db, project, connection, _, visitor, _, _ = setup
    for _ in range(MAX_VISITOR_BUSINESS_SCOPES):
        db.add(
            VisitorBusinessScope(
                project_id=project,
                visitor_id=visitor,
                connection_id=uuid4(),
                owner_fingerprint="0" * 64,
            )
        )
    db.commit()
    with pytest.raises(ConflictError, match="上限"):
        service.exchange(connection, sign(claims(setup)))
    assert db.query(VisitorBusinessScope).count() == MAX_VISITOR_BUSINESS_SCOPES
