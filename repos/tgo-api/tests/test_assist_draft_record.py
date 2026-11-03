"""Generated assist drafts must be durable and bound to the same staff turn."""

from uuid import uuid4
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.assist_draft_record import AssistDraftRecord
from app.schemas.chat import AssistDraftRequest, AssistDraftResponse
from app.schemas.assist_trace import AssistRunTrace
from app.schemas.shadow_review import ShadowSourceProof
from app.services.assist_draft_record import (
    get_assist_draft,
    resolve_delivery_draft,
    response_from_record,
    save_assist_draft,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    AssistDraftRecord.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_same_request_replays_saved_draft_and_rejects_changed_input(db):
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    requested_agent_id = uuid4()
    request = AssistDraftRequest(
        visitor_id=visitor_id,
        request_id=uuid4(),
        customer_message="这个包能装电脑吗？",
        source_message_id="visitor-1",
        source_client_msg_no="im-visitor-1",
    )
    response = AssistDraftResponse(
        draft="可放 13 英寸电脑。",
        generation_trace=[AssistRunTrace(
            phase="fact_answer",
            model_name="openai:gpt-4o",
            configured_prompt_sha256="a" * 64,
            api_template_sha256="c" * 64,
            requested_agent_id=requested_agent_id,
            request_system_prompt_sha256="b" * 64,
        )],
    )
    saved = save_assist_draft(
        db,
        project_id=project_id,
        staff_id=staff_id,
        request=request,
        fingerprint="original",
        response=response,
        source_proof=ShadowSourceProof(
            status="verified",
            checked_at=datetime.now(timezone.utc),
            channel_id=f"{visitor_id}-vtr",
            channel_type=251,
            client_msg_no="im-visitor-1",
            message_id="42",
            message_seq=2,
            message_timestamp=1710000000,
            content_sha256="a" * 64,
        ),
    )

    assert saved.project_id == project_id
    assert saved.visitor_id == visitor_id
    assert saved.source_message_id == "visitor-1"
    assert saved.source_proof["status"] == "verified"
    assert saved.source_proof["client_msg_no"] == "im-visitor-1"
    assert response_from_record(saved).draft_id == saved.id
    assert response_from_record(saved).draft == response.draft
    assert saved.response_data["generation_trace"][0]["model_name"] == "openai:gpt-4o"
    assert saved.response_data["generation_trace"][0]["requested_agent_id"] == str(requested_agent_id)
    assert response_from_record(saved).generation_trace[0].request_system_prompt_sha256 == "b" * 64
    assert response_from_record(saved).generation_trace[0].api_template_sha256 == "c" * 64
    assert response_from_record(saved).generation_trace[0].phase == "fact_answer"
    assert (
        get_assist_draft(
            db,
            project_id=project_id,
            staff_id=staff_id,
            request_id=request.request_id,
            fingerprint="original",
        ).id
        == saved.id
    )

    with pytest.raises(HTTPException) as conflict:
        get_assist_draft(
            db,
            project_id=project_id,
            staff_id=staff_id,
            request_id=request.request_id,
            fingerprint="different",
        )
    assert conflict.value.status_code == 409
    assert (
        get_assist_draft(
            db,
            project_id=project_id,
            staff_id=uuid4(),
            request_id=request.request_id,
            fingerprint="original",
        )
        is None
    )


def test_delivery_draft_requires_same_project_staff_visitor_and_source(db):
    project_id, visitor_id, staff_id = uuid4(), uuid4(), uuid4()
    request = AssistDraftRequest(
        visitor_id=visitor_id,
        customer_message="退货多久？",
        source_message_id="visitor-2",
    )
    saved = save_assist_draft(
        db,
        project_id=project_id,
        staff_id=staff_id,
        request=request,
        fingerprint="same",
        response=AssistDraftResponse(draft="七天内可申请。"),
    )

    assert (
        resolve_delivery_draft(
            db,
            draft_id=saved.id,
            project_id=project_id,
            staff_id=staff_id,
            visitor_id=visitor_id,
            source_message_id="visitor-2",
            payload_type=1,
        )
        == saved.id
    )
    for changed in (
        {"project_id": uuid4()},
        {"staff_id": uuid4()},
        {"visitor_id": uuid4()},
        {"source_message_id": "visitor-3"},
        {"source_message_id": None},
        {"payload_type": 2},
    ):
        kwargs = {
            "draft_id": saved.id,
            "project_id": project_id,
            "staff_id": staff_id,
            "visitor_id": visitor_id,
            "source_message_id": "visitor-2",
            "payload_type": 1,
            **changed,
        }
        with pytest.raises(HTTPException) as conflict:
            resolve_delivery_draft(db, **kwargs)
        assert conflict.value.status_code == 409
