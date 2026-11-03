"""Website data access requires the same current owner as the realtime gateway."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.services.im_access import issue_visitor_im_token
from app.services.visitor_end_grant import issue_visitor_end_token
from app.services.visitor_data_access import (
    authorize_visitor_data_channel,
    reauthorize_visitor_data,
    require_visitor_data_access,
)
from app.utils.encoding import build_visitor_channel_id
from tests.test_im_access import (
    records as records,
)  # shared disposable database, no runtime data


def bearer(visitor):
    return HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=issue_visitor_im_token(visitor, timedelta(hours=1)),
    )


def test_data_snapshot_is_bound_to_owner_and_never_serializes_secrets(records):
    db, _, _, platforms, visitors = records
    credentials = bearer(visitors[0])
    access = require_visitor_data_access(db, platforms[0], credentials)
    assert access.visitor_id == visitors[0].id
    assert reauthorize_visitor_data(db, access).id == visitors[0].id
    authorize_visitor_data_channel(
        access, build_visitor_channel_id(visitors[0].id), 251
    )
    authorize_visitor_data_channel(access, str(visitors[0].id), 1)
    assert credentials.credentials not in repr(access)
    assert credentials.credentials not in access.model_dump_json()
    assert "platform_api_key" not in access.model_dump()


@pytest.mark.parametrize("credential", ["missing", "end", "staff", "wrong_scheme"])
def test_platform_key_and_other_purposes_cannot_read_visitor_data(records, credential):
    db, _, staff, platforms, visitors = records
    proof = None
    if credential == "end":
        visitors[0].access_secret_digest = "a" * 64
        proof = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=issue_visitor_end_token(visitors[0]).get_secret_value(),
        )
    elif credential == "staff":
        from tests.test_im_access import staff_token

        proof = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=staff_token(staff[0])
        )
    elif credential == "wrong_scheme":
        proof = bearer(visitors[0])
        proof.scheme = "Basic"
    with pytest.raises(TGOAPIException) as denied:
        require_visitor_data_access(db, platforms[0], proof)
    assert denied.value.status_code == 401


@pytest.mark.parametrize("channel_type", [1, 251])
def test_a_current_visitor_cannot_read_another_channel(records, channel_type):
    db, _, _, platforms, visitors = records
    access = require_visitor_data_access(db, platforms[0], bearer(visitors[0]))
    identifier = (
        build_visitor_channel_id(visitors[1].id)
        if channel_type == 251
        else str(visitors[1].id)
    )
    with pytest.raises(TGOAPIException):
        authorize_visitor_data_channel(access, identifier, channel_type)


def test_signed_visitor_cannot_select_another_platform_by_its_public_key(records):
    db, _, _, platforms, visitors = records
    with pytest.raises(TGOAPIException):
        require_visitor_data_access(db, platforms[1], bearer(visitors[0]))


@pytest.mark.parametrize(
    "change",
    [
        "retired",
        "revision",
        "platform_disabled",
        "platform_key",
        "project_deleted",
        "nonwebsite",
    ],
)
def test_data_acquired_before_an_await_is_not_released_after_access_changes(
    records, change
):
    db, projects, _, platforms, visitors = records
    access = require_visitor_data_access(db, platforms[0], bearer(visitors[0]))
    with Session(db.bind) as other:
        from app.models import Platform, Project, Visitor

        visitor = other.get(Visitor, visitors[0].id)
        platform = other.get(Platform, platforms[0].id)
        if change == "retired":
            visitor.access_retired_at = datetime.now(UTC)
        elif change == "revision":
            visitor.access_revision += 1
        elif change == "platform_disabled":
            platform.is_active = False
        elif change == "platform_key":
            platform.api_key = "rotated"
        elif change == "nonwebsite":
            platform.type = "wecom_kf"
        else:
            other.get(Project, projects[0].id).deleted_at = datetime.now(UTC)
        other.commit()
    with pytest.raises(TGOAPIException):
        reauthorize_visitor_data(db, access)


def test_expired_im_grant_cannot_be_replaced_with_a_plain_holder_secret(records):
    db, _, _, platforms, visitors = records
    expired = issue_visitor_im_token(visitors[0], timedelta(seconds=-1))
    with pytest.raises(TGOAPIException):
        require_visitor_data_access(
            db,
            platforms[0],
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=expired),
        )
    with pytest.raises(TGOAPIException):
        require_visitor_data_access(
            db,
            platforms[0],
            HTTPAuthorizationCredentials(
                scheme="Bearer", credentials=SecretStr("holder").get_secret_value()
            ),
        )
