"""Execution receipts keep private inputs for the worker and hide them on the wire."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import executions
from app.database import get_db
from app.models.execution import (
    NodeExecution as NodeExecutionRow,
    WorkflowExecution as WorkflowExecutionRow,
)
from app.schemas.execution import (
    NodeFinishedData,
    NodeFinishedEvent,
    WorkflowExecution,
    WorkflowStartedData,
    WorkflowStartedEvent,
)


def test_history_receipt_exposes_only_input_evidence():
    raw = {"email": "person@example.test", "order_no": "PRIVATE-ORDER"}
    receipt = WorkflowExecution(
        id="run-a", project_id="project-a", workflow_id="workflow-a",
        status="completed", input=raw, started_at=datetime.now(UTC),
    )

    assert receipt.input == raw
    serialized = receipt.model_dump(mode="json")
    assert serialized["input"] == {"capture_status": "redacted", "field_count": 2}
    assert "person@example.test" not in str(serialized)
    assert "PRIVATE-ORDER" not in str(serialized)


def test_stream_start_event_never_replays_raw_inputs():
    raw = {"email": "person@example.test", "order_no": "PRIVATE-ORDER"}
    started = WorkflowStartedEvent(
        workflow_run_id="run-a", task_id="task-a",
        data=WorkflowStartedData(
            id="run-a", workflow_id="workflow-a", inputs=raw, created_at=1,
        ),
    )

    assert started.data.inputs == raw
    serialized = started.model_dump_json()
    assert '"capture_status":"redacted"' in serialized
    assert '"field_count":2' in serialized
    assert "person@example.test" not in serialized
    assert "PRIVATE-ORDER" not in serialized


def test_stream_node_event_never_replays_legacy_raw_inputs():
    event = NodeFinishedEvent(
        workflow_run_id="run-a", task_id="task-a",
        data=NodeFinishedData(
            id="node-a", node_id="input", node_type="input",
            inputs={"email": "person@example.test"},
            outputs={"email": "person@example.test"},
            status="succeeded", elapsed_time=0.1, finished_at=1,
        ),
    )
    serialized = event.model_dump_json()
    assert event.data.inputs == {"email": "person@example.test"}
    assert '"capture_status":"redacted"' in serialized
    assert "person@example.test" not in serialized
    assert event.model_dump(mode="json")["data"]["outputs"] == {
        "capture_status": "redacted", "field_count": 1,
    }


def test_already_redacted_node_input_stays_redacted_without_recounting_marker():
    event = NodeFinishedEvent(
        workflow_run_id="run-a", task_id="task-a",
        data=NodeFinishedData(
            id="node-a", node_id="input", node_type="input",
            inputs={"capture_status": "redacted", "unexpected": "private-value"},
            status="succeeded", elapsed_time=0.1, finished_at=1,
        ),
    )
    assert event.model_dump(mode="json")["data"]["inputs"] == {
        "capture_status": "redacted",
    }
    assert "private-value" not in event.model_dump_json()


def test_public_node_receipt_keeps_answer_and_safe_business_query_reference():
    query = NodeFinishedEvent(
        workflow_run_id="run-a", task_id="task-a",
        data=NodeFinishedData(
            id="node-a", node_id="query", node_type="business_query",
            outputs={
                "query_id": "2a1ab64c-8833-4cad-bc1e-799d26b2505a",
                "connection_id": "0ddba593-b4af-4855-a1c5-a64c2822be38",
                "operation": "order_query", "body": "private-provider-result",
            },
            status="succeeded", elapsed_time=0.1, finished_at=1,
        ),
    )
    assert query.model_dump(mode="json")["data"]["outputs"] == {
        "query_id": "2a1ab64c-8833-4cad-bc1e-799d26b2505a",
        "connection_id": "0ddba593-b4af-4855-a1c5-a64c2822be38",
        "operation": "order_query",
    }
    assert "private-provider-result" not in query.model_dump_json()
    answer = NodeFinishedEvent(
        workflow_run_id="run-a", task_id="task-a",
        data=NodeFinishedData(
            id="node-b", node_id="answer", node_type="answer",
            outputs={"result": "客户可见答复"},
            status="succeeded", elapsed_time=0.1, finished_at=1,
        ),
    )
    assert answer.model_dump(mode="json")["data"]["outputs"] == {
        "result": "客户可见答复",
    }


def test_malformed_legacy_business_query_output_is_redacted():
    event = NodeFinishedEvent(
        workflow_run_id="run-a", task_id="task-a",
        data=NodeFinishedData(
            id="node-a", node_id="query", node_type="business_query",
            outputs={"operation": {"private": "value"}, "body": "private-result"},
            status="succeeded", elapsed_time=0.1, finished_at=1,
        ),
    )
    assert event.model_dump(mode="json")["data"]["outputs"] == {
        "capture_status": "redacted", "field_count": 2,
    }
    assert "private-result" not in event.model_dump_json()


@pytest.mark.asyncio
async def test_execution_status_http_response_redacts_input():
    raw = {"email": "person@example.test", "order_no": "PRIVATE-ORDER"}
    row = WorkflowExecutionRow(
        id="run-a", project_id="project-a", workflow_id="workflow-a",
        status="completed", input=raw, started_at=datetime.now(UTC),
        employee_agent_id="a471a04d-68b9-4574-a2da-2b1776168ed8",
        release_id="release-a", workflow_version=3, definition_digest="a" * 64,
        ai_request_id="ai-run-a", business_request_id="business-a",
        visitor_session_id="session-a",
        node_executions=[NodeExecutionRow(
            id="node-a", project_id="project-a", execution_id="run-a",
            node_id="input", node_type="input", status="completed",
            input={"email": "legacy-private@example.test"},
            output={"email": "legacy-private@example.test"},
            started_at=datetime.now(UTC),
        )],
    )
    result = SimpleNamespace(scalar_one_or_none=lambda: row)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    app = FastAPI()
    app.include_router(executions.router, prefix="/v1/workflows")

    async def test_session():
        yield session

    app.dependency_overrides[get_db] = test_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/workflows/executions/run-a?project_id=project-a")

    assert response.status_code == 200
    assert response.json()["input"] == {"capture_status": "redacted", "field_count": 2}
    assert response.json()["node_executions"][0]["input"] == {
        "capture_status": "redacted", "field_count": 1,
    }
    assert response.json()["node_executions"][0]["output"] == {
        "capture_status": "redacted", "field_count": 1,
    }
    assert response.json()["employee_agent_id"] == row.employee_agent_id
    assert response.json()["release_id"] == row.release_id
    assert response.json()["workflow_version"] == row.workflow_version
    assert response.json()["definition_digest"] == row.definition_digest
    assert response.json()["ai_request_id"] == row.ai_request_id
    assert response.json()["business_request_id"] == row.business_request_id
    assert response.json()["visitor_session_id"] == row.visitor_session_id
    assert "person@example.test" not in response.text
    assert "PRIVATE-ORDER" not in response.text
    assert "legacy-private@example.test" not in response.text
    assert row.input == raw
    assert row.node_executions[0].input == {
        "email": "legacy-private@example.test",
    }
