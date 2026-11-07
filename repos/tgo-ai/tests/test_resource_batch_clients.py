"""Resource lookups must account for requested IDs and keep errors private."""

import json
from uuid import UUID

import httpx
import pytest

from app.exceptions import NotFoundError, ValidationError
from app.services.rag_service import RAGServiceClient
from app.services.workflow_service import WorkflowServiceClient

COLLECTION = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
PRIVATE = "SYNTHETIC-UPSTREAM-PRIVATE"


def collection(identifier=COLLECTION):
    return {
        "id": identifier,
        "display_name": "产品资料",
        "description": None,
        "collection_metadata": {"language": "zh"},
        "created_at": "2026-11-06T00:00:00Z",
        "updated_at": "2026-11-06T00:00:00Z",
    }


def workflow(identifier="workflow-1"):
    return {
        "id": identifier,
        "name": "产品咨询",
        "status": "active",
        "version": 2,
        "published_version": 1,
        "updated_at": "2026-11-06T00:00:00Z",
    }


@pytest.fixture
def http_reply(monkeypatch):
    original = httpx.AsyncClient
    requests = []

    def configure(body=None, *, status=200, content=None, error=None):
        def handle(request):
            requests.append(request)
            if error is not None:
                raise error(PRIVATE, request=request)
            if content is not None:
                return httpx.Response(status, content=content)
            return httpx.Response(status, json=body)

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: original(
                transport=httpx.MockTransport(handle), **kwargs
            ),
        )
        return requests

    return configure


def client_for(kind):
    client = RAGServiceClient() if kind == "rag" else WorkflowServiceClient()
    client.base_url = "https://lookup.example.test"
    return client


async def lookup(kind, client=None):
    client = client or client_for(kind)
    if kind == "rag":
        return await client.get_collections_batch([COLLECTION], PROJECT)
    return await client.get_workflows_batch(["workflow-1"], PROJECT)


def assert_private(error):
    assert PRIVATE not in str(error)
    assert PRIVATE not in json.dumps(error.details)
    assert error.__cause__ is None


@pytest.mark.asyncio
async def test_collection_batch_preserves_data_order_and_request(http_reply):
    requests = http_reply(
        {
            "collections": [collection(OTHER), collection()],
            "not_found": [],
            "total_requested": 2,
            "total_found": 2,
        }
    )
    result = await client_for("rag").get_collections_batch(
        [COLLECTION, OTHER], PROJECT
    )
    assert [str(row.id) for row in result.collections] == [OTHER, COLLECTION]
    assert result.collections[0].collection_metadata == {"language": "zh"}
    assert result.not_found == []
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/collections/batch"
    assert request.url.params["project_id"] == PROJECT
    assert json.loads(request.content) == {
        "collection_ids": [COLLECTION, OTHER]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [True, False])
async def test_duplicate_request_ids_keep_the_provider_contract(
    http_reply, found
):
    http_reply(
        {
            "collections": [collection()] if found else [],
            "not_found": [] if found else [COLLECTION, COLLECTION],
        }
    )
    result = await client_for("rag").get_collections_batch(
        [COLLECTION, COLLECTION], PROJECT
    )
    assert len(result.collections) == int(found)
    assert result.not_found == ([] if found else [COLLECTION, COLLECTION])


@pytest.mark.asyncio
async def test_equivalent_uuid_spelling_does_not_change_the_request(
    http_reply,
):
    identifier = "abcdefab-cdef-4abc-8def-abcdefabcdef"
    requests = http_reply(
        {"collections": [collection(identifier)], "not_found": []}
    )
    result = await client_for("rag").get_collections_batch(
        [identifier.upper()], PROJECT
    )
    assert result.collections[0].id == UUID(identifier)
    assert json.loads(requests[0].content)["collection_ids"] == [
        identifier.upper()
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "duplicate_found",
        "unexpected_found",
        "missing_result",
        "overlap",
        "foreign_missing",
        "duplicate_missing",
        "invalid_missing_id",
        "bad_json",
        "array",
        "null",
        "bad_fields",
        "missing_fields",
    ],
)
async def test_collection_batch_rejects_unaccounted_or_invalid_results(
    http_reply, bad
):
    body = {"collections": [collection()], "not_found": []}
    if bad == "duplicate_found":
        body["collections"].append(collection())
    elif bad == "unexpected_found":
        body["collections"] = [collection(OTHER)]
    elif bad == "missing_result":
        body["collections"] = []
    elif bad == "overlap":
        body["not_found"] = [COLLECTION]
    elif bad == "foreign_missing":
        body["not_found"] = [OTHER]
    elif bad == "duplicate_missing":
        body = {"collections": [], "not_found": [COLLECTION, COLLECTION]}
    elif bad == "invalid_missing_id":
        body = {"collections": [], "not_found": [PRIVATE]}
    elif bad == "array":
        body = [PRIVATE]
    elif bad == "null":
        body = None
    elif bad == "bad_fields":
        body["collections"][0]["id"] = PRIVATE
    elif bad == "missing_fields":
        body = {"collections": []}
    http_reply(body, content=b"not-json" if bad == "bad_json" else None)
    with pytest.raises(ValidationError) as caught:
        await lookup("rag")
    assert_private(caught.value)


@pytest.mark.asyncio
async def test_empty_success_cannot_validate_collection_existence(http_reply):
    http_reply({"collections": [], "not_found": []})
    with pytest.raises(ValidationError):
        await client_for("rag").validate_collections_exist(
            [COLLECTION], PROJECT
        )


@pytest.mark.asyncio
async def test_workflow_batch_keeps_partial_results_and_query_parameters(
    http_reply,
):
    requests = http_reply([workflow()])
    result = await client_for("workflow").get_workflows_batch(
        ["workflow-1", "workflow-2"], PROJECT
    )
    assert [row.id for row in result] == ["workflow-1"]
    assert result[0].published_version == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/v1/workflows/batch"
    assert requests[0].url.params.get_list("workflow_ids") == [
        "workflow-1",
        "workflow-2",
    ]
    assert requests[0].url.params["project_id"] == PROJECT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    ["duplicate", "unexpected", "object", "null", "bad_item", "bad_json"],
)
async def test_workflow_batch_rejects_foreign_duplicate_or_invalid_records(
    http_reply, bad
):
    body = [workflow()]
    if bad == "duplicate":
        body.append(workflow())
    elif bad == "unexpected":
        body = [workflow("workflow-foreign")]
    elif bad == "object":
        body = {"workflows": body}
    elif bad == "null":
        body = None
    elif bad == "bad_item":
        body = [PRIVATE]
    http_reply(body, content=b"not-json" if bad == "bad_json" else None)
    with pytest.raises(ValidationError) as caught:
        await lookup("workflow")
    assert_private(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["rag", "workflow"])
@pytest.mark.parametrize("status", [201, 204, 302, 400, 422, 500])
async def test_http_failure_or_unexpected_success_is_a_private_domain_error(
    http_reply, kind, status
):
    http_reply([PRIVATE], status=status)
    with pytest.raises(ValidationError) as caught:
        await lookup(kind)
    assert caught.value.details["status_code"] == status
    assert_private(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["rag", "workflow"])
async def test_error_object_is_not_returned_in_validation_details(
    http_reply, kind
):
    http_reply({"detail": PRIVATE}, status=422)
    with pytest.raises(ValidationError) as caught:
        await lookup(kind)
    assert_private(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["rag", "workflow"])
@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_transport_failure_keeps_request_details_private(
    http_reply, kind, error
):
    http_reply(error=error)
    with pytest.raises(NotFoundError) as caught:
        await lookup(kind)
    assert "resource_id" not in caught.value.details
    assert_private(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["rag", "workflow"])
async def test_client_entry_failure_is_also_normalized(monkeypatch, kind):
    class Unavailable:
        async def __aenter__(self):
            raise httpx.ConnectError(PRIVATE)

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: Unavailable())
    with pytest.raises(NotFoundError) as caught:
        await lookup(kind)
    assert_private(caught.value)


@pytest.mark.asyncio
async def test_rag_404_preserves_explicit_missing_results(http_reply):
    http_reply(content=b"not-json", status=404)
    result = await lookup("rag")
    assert result.collections == [] and result.not_found == [COLLECTION]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["rag", "workflow"])
async def test_empty_requests_do_not_open_a_connection(http_reply, kind):
    requests = http_reply()
    client = client_for(kind)
    if kind == "rag":
        result = await client.get_collections_batch([], PROJECT)
        assert result.collections == [] and result.not_found == []
    else:
        assert await client.get_workflows_batch([], PROJECT) == []
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["rag", "workflow"])
async def test_missing_resource_errors_keep_the_actual_missing_identifiers(
    http_reply, kind
):
    http_reply(
        {"collections": [], "not_found": [COLLECTION]} if kind == "rag" else []
    )
    client = client_for(kind)
    with pytest.raises(NotFoundError) as caught:
        if kind == "rag":
            await client.validate_collections_exist([COLLECTION], PROJECT)
        else:
            await client.validate_workflows_exist(["workflow-1"], PROJECT)
    assert caught.value.details["missing_ids"] == (
        [COLLECTION] if kind == "rag" else ["workflow-1"]
    )
    assert "resource_id" not in caught.value.details
