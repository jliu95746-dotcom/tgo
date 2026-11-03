"""Only admin-selected, project-owned classification fields leave the API."""

from types import SimpleNamespace
from uuid import uuid4
import json

import pytest
from pydantic import ValidationError

from app.core.exceptions import TGOAPIException
from app.api.v1.endpoints.message_analysis import (
    export_staff_intent_evaluation_predictions,
)
from app.core.security import get_current_active_user
from app.main import app
from app.models import Staff
from app.schemas.intent_evaluation_export import IntentEvaluationExportRequest
from app.services.intent_evaluation_export import IntentEvaluationExportService


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None

    def execute(self, statement):
        self.statement = statement
        return FakeResult(self.rows)


def _record(_project_id, _platform_id, _case_id, **overrides):
    fields = {
        "id": _case_id,
        "project_id": _project_id,
        "platform_id": _platform_id,
        "analysis_mode": "shadow",
        "intent": "product_inquiry",
        "confidence": 0.92,
        "recommended_route": "auto_reply",
        "need_human": False,
        "taxonomy_version": "v1",
        "classifier_version": "intent-v1",
        "policy_version": "routing-v1",
        "source_message_id": "private-source-123",
        "entities": {"phone": "private-data"},
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_export_matches_g3_prediction_contract_without_customer_fields():
    project_id, platform_id = uuid4(), uuid4()
    first, second = uuid4(), uuid4()
    db = FakeSession(
        [
            _record(
                project_id,
                platform_id,
                second,
                intent="complaint",
                recommended_route="human_handoff",
                need_human=True,
            ),
            _record(project_id, platform_id, first),
        ]
    )
    request = IntentEvaluationExportRequest(
        platform_id=platform_id,
        analysis_mode="shadow",
        case_ids=(first, second),
    )

    output = IntentEvaluationExportService(db).export(project_id, request)
    lines = [json.loads(line) for line in output.splitlines()]

    assert [line["case_id"] for line in lines] == [str(first), str(second)]
    assert set(lines[0]) == {
        "case_id",
        "intent",
        "confidence",
        "recommended_route",
        "need_human",
        "taxonomy_version",
        "classifier_version",
        "policy_version",
    }
    assert lines[1]["recommended_route"] == "human_handoff"
    assert "private-source" not in output and "private-data" not in output
    sql = str(db.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "api_message_intent_results.project_id" in sql
    assert "api_message_intent_results.platform_id" in sql
    assert "api_message_intent_results.analysis_mode" in sql
    assert "source_message_id" not in sql and "entities" not in sql


@pytest.mark.parametrize(
    "bad_fields",
    [
        {"id": uuid4()},
        {"project_id": uuid4()},
        {"platform_id": uuid4()},
        {"analysis_mode": "active"},
    ],
)
def test_missing_or_foreign_result_fails_whole_export(bad_fields):
    project_id, platform_id, case_id = uuid4(), uuid4(), uuid4()
    db = FakeSession([_record(project_id, platform_id, case_id, **bad_fields)])
    request = IntentEvaluationExportRequest(
        platform_id=platform_id,
        analysis_mode="shadow",
        case_ids=(case_id,),
    )
    with pytest.raises(TGOAPIException) as error:
        IntentEvaluationExportService(db).export(project_id, request)
    assert error.value.status_code == 404


def test_request_rejects_duplicate_and_too_many_case_ids():
    case_id = uuid4()
    with pytest.raises(ValidationError):
        IntentEvaluationExportRequest(
            platform_id=uuid4(), analysis_mode="shadow", case_ids=(case_id, case_id)
        )
    with pytest.raises(ValidationError):
        IntentEvaluationExportRequest(
            platform_id=uuid4(),
            analysis_mode="shadow",
            case_ids=tuple(uuid4() for _ in range(501)),
        )


def test_invalid_persisted_prediction_fails_without_partial_output():
    project_id, platform_id, case_id = uuid4(), uuid4(), uuid4()
    db = FakeSession([_record(project_id, platform_id, case_id, need_human=True)])
    request = IntentEvaluationExportRequest(
        platform_id=platform_id, analysis_mode="shadow", case_ids=(case_id,)
    )
    with pytest.raises(TGOAPIException) as error:
        IntentEvaluationExportService(db).export(project_id, request)
    assert error.value.status_code == 409


def test_mixed_classifier_versions_cannot_form_one_evaluation_file():
    project_id, platform_id = uuid4(), uuid4()
    first, second = uuid4(), uuid4()
    db = FakeSession(
        [
            _record(project_id, platform_id, first),
            _record(project_id, platform_id, second, classifier_version="intent-v2"),
        ]
    )
    request = IntentEvaluationExportRequest(
        platform_id=platform_id, analysis_mode="shadow", case_ids=(first, second)
    )
    with pytest.raises(TGOAPIException) as error:
        IntentEvaluationExportService(db).export(project_id, request)
    assert error.value.status_code == 409


def test_admin_endpoint_returns_download_without_caching(monkeypatch):
    project_id, platform_id, case_id = uuid4(), uuid4(), uuid4()
    request = IntentEvaluationExportRequest(
        platform_id=platform_id, analysis_mode="shadow", case_ids=(case_id,)
    )
    db = FakeSession([])
    seen = []

    def fake_export(self, scope, selection):
        seen.append((scope, selection))
        return '{"case_id":"test"}\n'

    monkeypatch.setattr(IntentEvaluationExportService, "export", fake_export)
    response = export_staff_intent_evaluation_predictions(
        request, current_user=SimpleNamespace(project_id=project_id), db=db
    )
    assert seen == [(project_id, request)]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["content-disposition"].endswith(
        'intent-g3-predictions.jsonl"'
    )


def test_http_export_requires_admin_and_uses_signed_in_project(client, db_override):
    project_id, platform_id, case_id = uuid4(), uuid4(), uuid4()
    staff = Staff(
        id=uuid4(),
        project_id=project_id,
        username="evaluation-reviewer",
        password_hash="test-only",
        name="Reviewer",
        role="user",
    )
    db_override.session = FakeSession([_record(project_id, platform_id, case_id)])
    app.dependency_overrides[get_current_active_user] = lambda: staff
    payload = {
        "platform_id": str(platform_id),
        "analysis_mode": "shadow",
        "case_ids": [str(case_id)],
    }
    denied = client.post(
        "/v1/message-analysis/staff/intent-evaluation-predictions", json=payload
    )
    assert denied.status_code == 403

    staff.role = "admin"
    allowed = client.post(
        "/v1/message-analysis/staff/intent-evaluation-predictions", json=payload
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["cache-control"] == "no-store"
    assert json.loads(allowed.text)["case_id"] == str(case_id)
