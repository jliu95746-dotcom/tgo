"""A retained end-only grant must not retain access to the old customer's data."""

from datetime import timedelta
from uuid import uuid4

import pytest
from jose import jwt
from pydantic import SecretStr

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.schemas.visitor_access import VisitorAccessInput, VisitorAccessEndRequest
from app.services.visitor_access import registration_access, require_access, end_access
from app.services.im_access import authenticate_im_actor
from app.services.visitor_end_grant import issue_visitor_end_token
from tests.test_visitor_access import access_fixture as access_fixture
from tests.test_business_customer_identity import setup as setup


def grant_fixture(access_fixture):
    _, db, visitor, _, _ = access_fixture
    secret, _ = registration_access(db, visitor, VisitorAccessInput(), new=True)
    db.commit()
    return db, visitor, secret, issue_visitor_end_token(visitor)


def test_end_token_can_only_end_and_repeat_its_original_owned_access(access_fixture):
    db, visitor, _, token = grant_fixture(access_fixture)
    # Even an internal caller bypassing HTTP field-length validation gets no read authority.
    for proof in (VisitorAccessInput.model_construct(visitor_access_token=token), VisitorAccessInput(im_token=token)):
        with pytest.raises(TGOAPIException):
            require_access(db, visitor, proof)
    with pytest.raises(TGOAPIException):
        authenticate_im_actor(db, f"{visitor.id}-vtr", token.get_secret_value())
    with pytest.raises(TGOAPIException):
        registration_access(db, visitor, VisitorAccessInput.model_construct(visitor_access_token=token), new=False)
    end_access(db, visitor, VisitorAccessInput(), end_token=token)
    revision = visitor.access_revision
    end_access(db, visitor, VisitorAccessInput(), end_token=token)
    assert visitor.access_retired_at and visitor.access_revision == revision


@pytest.mark.parametrize("change", ["visitor", "project", "platform", "revision", "holder", "purpose", "expiry", "signature"])
def test_end_token_rejects_forged_or_stale_original_coordinates(access_fixture, change):
    db, visitor, _, token = grant_fixture(access_fixture)
    claims = jwt.get_unverified_claims(token.get_secret_value())
    if change in {"visitor", "project", "platform"}:
        claims[{"visitor":"sub", "project":"project_id", "platform":"platform_id"}[change]] = str(uuid4())
    elif change == "revision":
        visitor.access_revision += 1
        db.commit()
    elif change == "holder":
        visitor.access_secret_digest = "f" * 64
        db.commit()
    elif change == "purpose":
        claims["type"] = "visitor_im"
    elif change == "expiry":
        token = issue_visitor_end_token(visitor, lifetime=timedelta(seconds=-1))
    if change not in {"expiry", "revision", "holder"}:
        token = SecretStr(jwt.encode(claims, settings.SECRET_KEY if change != "signature" else "synthetic-wrong-signer" * 3, algorithm=settings.ALGORITHM))
    with pytest.raises(TGOAPIException):
        end_access(db, visitor, VisitorAccessInput(), end_token=token)
    db.refresh(visitor)
    assert visitor.access_retired_at is None


def test_end_request_never_serializes_or_logs_its_end_token(access_fixture):
    _, visitor, _, token = grant_fixture(access_fixture)
    request = VisitorAccessEndRequest(platform_api_key="public-fixture-key", visitor_id=visitor.id, visitor_end_token=token)
    assert request.model_dump(mode="json") == {"visitor_id": str(visitor.id)}
    assert token.get_secret_value() not in repr(request)
