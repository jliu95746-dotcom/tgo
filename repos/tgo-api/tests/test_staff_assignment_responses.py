"""Untrusted dispatcher output cannot break a transfer or escape into logs."""

import asyncio
import json
from uuid import UUID
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy.orm import Session

from app.models import Visitor, VisitorAssignmentRule
from app.services import transfer_service
from app.services.ai_client import AIServiceClient, ai_client

PRIVATE = "synthetic-private-dispatcher-content"
pytestmark = pytest.mark.asyncio


@pytest.fixture
def dispatcher(monkeypatch):
    project = uuid4()
    candidates = [
        transfer_service.StaffCandidate(
            uuid4(), "specialist", None, None, "online", 3
        ),
        transfer_service.StaffCandidate(
            uuid4(), "available", None, None, "online", 0
        ),
    ]
    rule = VisitorAssignmentRule(
        project_id=project,
        ai_provider_id=uuid4(),
        model="fixture-dispatcher",
        prompt="Synthetic dispatcher policy",
        llm_assignment_enabled=True,
    )
    visitor = Visitor(id=uuid4(), project_id=project, name="synthetic visitor")
    complete = AsyncMock()
    monkeypatch.setattr(ai_client, "chat_completions", complete)

    async def run():
        return await transfer_service._llm_assign_staff(
            MagicMock(spec=Session),
            project,
            visitor,
            "Need assistance",
            candidates,
            rule,
        )

    return run, complete, candidates, rule


def completion(content):
    return {"choices": [{"message": {"content": content}}]}


def assert_safe_fallback(result, candidates):
    assert result["selected_staff_id"] == candidates[1].id
    assert isinstance(result["reasoning"], str)
    assert PRIVATE not in result["reasoning"]


@pytest.mark.parametrize("fence", [None, "json", ""])
async def test_valid_selection_and_existing_markdown_forms(dispatcher, fence):
    run, complete, candidates, _rule = dispatcher
    content = json.dumps(
        {
            "selected_staff_id": str(candidates[0].id),
            "reasoning": "Understands the requested product",
        }
    )
    if fence is not None:
        content = "```" + fence + "\n" + content + "\n```"
    complete.return_value = completion(content)
    result = await run()
    assert result["selected_staff_id"] == candidates[0].id
    assert result["reasoning"] == "Understands the requested product"
    assert result["llm_response"] == content


@pytest.mark.parametrize(
    "reason",
    [
        {"private": PRIVATE},
        [PRIVATE],
        7,
        True,
        PRIVATE + "\x00",
        PRIVATE + "\ud800",
    ],
)
async def test_invalid_reason_cannot_become_a_database_value(
    dispatcher, reason
):
    run, complete, candidates, _rule = dispatcher
    complete.return_value = completion(
        json.dumps(
            {
                "selected_staff_id": str(candidates[0].id),
                "reasoning": reason,
            }
        )
    )
    result = await run()
    assert_safe_fallback(result, candidates)


@pytest.mark.parametrize("reason", [None, "Clear explanation"])
async def test_reason_is_optional_text(dispatcher, reason):
    run, complete, candidates, _rule = dispatcher
    complete.return_value = completion(
        json.dumps(
            {
                "selected_staff_id": str(candidates[0].id),
                "reasoning": reason,
            }
        )
    )
    result = await run()
    assert result["selected_staff_id"] == candidates[0].id
    assert result["reasoning"] == reason


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"choices": []},
        {"choices": PRIVATE},
        {"choices": [None]},
        {"choices": [{}]},
        completion(None),
        completion({"private": PRIVATE}),
        completion([PRIVATE]),
        completion(7),
    ],
)
async def test_malformed_envelope_keeps_assignment_available(
    dispatcher, caplog, response
):
    run, complete, candidates, _rule = dispatcher
    complete.return_value = response
    with caplog.at_level("INFO"):
        result = await run()
    assert_safe_fallback(result, candidates)
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


@pytest.mark.parametrize("text", ["{", "[]", "null", '"text"', "7", "{}"])
async def test_invalid_selection_json_uses_load_balancing(dispatcher, text):
    run, complete, candidates, _rule = dispatcher
    complete.return_value = completion(text)
    result = await run()
    assert_safe_fallback(result, candidates)


@pytest.mark.parametrize("selected", [PRIVATE, str(uuid4())])
async def test_outside_candidate_id_and_reason_are_not_logged(
    dispatcher, caplog, selected
):
    run, complete, candidates, _rule = dispatcher
    complete.return_value = completion(
        json.dumps(
            {
                "selected_staff_id": selected,
                "reasoning": PRIVATE,
            }
        )
    )
    with caplog.at_level("INFO"):
        result = await run()
    assert_safe_fallback(result, candidates)
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


@pytest.mark.parametrize("text", ["```json\n{}", "```python\n{}\n```"])
async def test_invalid_json_fence_uses_load_balancing(dispatcher, text):
    run, complete, candidates, _rule = dispatcher
    complete.return_value = completion(text)
    assert_safe_fallback(await run(), candidates)


async def test_valid_private_reason_is_stored_but_not_logged(
    dispatcher, caplog
):
    run, complete, candidates, _rule = dispatcher
    complete.return_value = completion(
        json.dumps(
            {
                "selected_staff_id": str(candidates[0].id),
                "reasoning": PRIVATE,
            }
        )
    )
    with caplog.at_level("INFO"):
        result = await run()
    assert result["reasoning"] == PRIVATE
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


@pytest.mark.parametrize("content", [PRIVATE + "\x00", PRIVATE + "\ud800"])
async def test_non_storable_raw_text_is_discarded(dispatcher, content):
    run, complete, candidates, _rule = dispatcher
    complete.return_value = completion(content)
    result = await run()
    assert_safe_fallback(result, candidates)
    assert result["llm_response"] is None


async def test_provider_failure_reason_is_not_persisted_or_logged(
    dispatcher, caplog
):
    run, complete, candidates, _rule = dispatcher
    complete.side_effect = RuntimeError(PRIVATE)
    with caplog.at_level("INFO"):
        result = await run()
    assert_safe_fallback(result, candidates)
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


async def test_missing_provider_does_not_call_ai(dispatcher):
    run, complete, candidates, rule = dispatcher
    rule.ai_provider_id = None
    result = await run()
    assert_safe_fallback(result, candidates)
    complete.assert_not_awaited()


async def test_cancellation_is_not_converted_to_a_new_assignment(dispatcher):
    run, complete, _candidates, _rule = dispatcher
    complete.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await run()


async def test_ai_client_error_body_contract_survives_without_logging(caplog):
    client = AIServiceClient()
    body = {"error": {"message": PRIVATE}}
    response = httpx.Response(503, json=body)
    with caplog.at_level("INFO"), pytest.raises(HTTPException) as rejected:
        await client._handle_response(response)
    assert rejected.value.status_code == 503
    assert rejected.value.detail == body
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


@pytest.mark.parametrize(
    "error_type, expected_status",
    [(httpx.ConnectError, 502), (httpx.TimeoutException, 504)],
)
async def test_ai_client_request_errors_do_not_format_private_exception(
    monkeypatch, caplog, error_type, expected_status
):
    client = AIServiceClient()
    monkeypatch.setattr(client, "_get_headers", lambda: {})
    session = AsyncMock()
    session.request.side_effect = error_type(PRIVATE)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    with caplog.at_level("INFO"), pytest.raises(HTTPException) as rejected:
        await client._make_request("POST", "/synthetic-dispatcher")
    assert rejected.value.status_code == expected_status
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


async def test_ai_client_transmits_query_without_logging_it(
    monkeypatch, caplog
):
    client = AIServiceClient()
    client.base_url = "http://synthetic-ai.test"
    monkeypatch.setattr(client, "_get_headers", lambda: {})
    original_client = httpx.AsyncClient
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200,
            stream=httpx.ByteStream(json.dumps({"result": PRIVATE}).encode()),
        )

    def configured_client(**kwargs):
        return original_client(
            transport=httpx.MockTransport(respond), **kwargs
        )

    monkeypatch.setattr(httpx, "AsyncClient", configured_client)
    with caplog.at_level("INFO"):
        response = await client._make_request(
            "GET", "/synthetic", params={"filter": PRIVATE}
        )
    assert seen[0].url.params["filter"] == PRIVATE
    assert response.json() == {"result": PRIVATE}
    records = [
        record for record in caplog.records if record.name == "ai_client"
    ]
    assert records
    assert PRIVATE not in repr([vars(record) for record in records])
    assert all(UUID(record.request_id) for record in records)
