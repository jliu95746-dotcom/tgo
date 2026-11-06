"""RAG HTTP contracts reject malformed success and protect log contents."""

import asyncio
from datetime import timedelta
from io import BytesIO
import json

from fastapi import HTTPException, UploadFile
import httpx
from pydantic import SecretStr
import pytest
from starlette.datastructures import Headers

from app.core.config import settings
from app.services.rag_client import RAGServiceClient

REAL_HTTP_CLIENT = httpx.AsyncClient
PRIVATE = "synthetic-private-rag-query-and-error"
pytestmark = pytest.mark.asyncio


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)

    def build(*, body=None, status=200, raw=None, error=None):
        calls = []

        async def receive(request):
            calls.append(request)
            if error is not None:
                raise error
            if raw is not None:
                response = httpx.Response(status, content=raw)
            else:
                response = httpx.Response(status, json=body)
            # MockTransport returns already-buffered responses; supply the
            # request timing metadata provided by an actual HTTP connection.
            response.elapsed = timedelta(milliseconds=1)
            return response

        def make_client(*args, **kwargs):
            return REAL_HTTP_CLIENT(
                *args,
                **kwargs,
                transport=httpx.MockTransport(receive),
            )

        monkeypatch.setattr(httpx, "AsyncClient", make_client)
        client = RAGServiceClient()
        client.base_url = "http://rag.invalid"
        client.timeout = 2
        return client, calls

    return build


@pytest.mark.parametrize("body", [None, [], "text", 12, False])
async def test_object_endpoint_rejects_non_object_success(gateway, body):
    client, calls = gateway(body=body)
    with pytest.raises(HTTPException) as rejected:
        await client.get_collection("project", "collection")
    assert rejected.value.status_code == 502
    assert len(calls) == 1


@pytest.mark.parametrize("raw", [b"not json", b"\xff\xfe\xff"])
async def test_object_endpoint_rejects_non_json_success(gateway, raw):
    client, _ = gateway(raw=raw)
    with pytest.raises(HTTPException) as rejected:
        await client.get_file("project", "file")
    assert rejected.value.status_code == 502


@pytest.mark.parametrize("pagination", [None, [], "bad", 3])
@pytest.mark.parametrize(
    "method",
    [
        "list_collections",
        "list_files",
        "list_collection_pages",
    ],
)
async def test_malformed_pagination_is_a_gateway_error(
    gateway,
    pagination,
    method,
):
    client, _ = gateway(body={"data": [], "pagination": pagination})
    kwargs = {"project_id": "project"}
    if method == "list_collection_pages":
        kwargs["collection_id"] = "collection"
    with pytest.raises(HTTPException) as rejected:
        await getattr(client, method)(**kwargs)
    assert rejected.value.status_code == 502


async def test_valid_json_and_pagination_keep_the_existing_wire_values(
    gateway,
):
    original = {
        "data": [{"id": "collection", "display_name": "商品资料"}],
        "pagination": {"total": 1, "has_previous": True, "has_more": False},
    }
    client, calls = gateway(body=original)
    result = await client.list_collections(
        "project",
        display_name="商品",
        collection_type="file",
        tags="售后",
        limit=2,
        offset=1,
    )
    assert result == {
        "data": original["data"],
        "pagination": {"total": 1, "has_prev": True, "has_more": False},
    }
    assert dict(calls[0].url.params) == {
        "project_id": "project",
        "display_name": "商品",
        "collection_type": "file",
        "tags": "售后",
        "limit": "2",
        "offset": "1",
    }


@pytest.mark.parametrize("method", ["delete_collection", "delete_file"])
@pytest.mark.parametrize("status", [200, 204])
async def test_delete_returns_no_body_after_a_success(gateway, method, status):
    client, calls = gateway(body={"deleted": True}, status=status)
    assert await getattr(client, method)("project", "resource") is None
    assert calls[0].method == "DELETE"
    assert calls[0].url.params["project_id"] == "project"


@pytest.mark.parametrize("status", [400, 403, 404, 429, 503])
async def test_upstream_errors_keep_status_and_detail_but_not_log_content(
    gateway,
    caplog,
    status,
):
    body = {"error": {"message": PRIVATE, "code": "controlled"}}
    client, _ = gateway(body=body, status=status)
    with caplog.at_level("INFO"), pytest.raises(HTTPException) as rejected:
        await client.list_collections("project", display_name=PRIVATE)
    assert rejected.value.status_code == status
    assert rejected.value.detail == body
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


@pytest.mark.parametrize(
    "kind,status",
    [
        (httpx.ConnectError, 502),
        (httpx.ReadTimeout, 504),
    ],
)
async def test_transport_failures_keep_controlled_errors_and_safe_logs(
    gateway,
    caplog,
    kind,
    status,
):
    client, _ = gateway(error=kind(PRIVATE))
    with caplog.at_level("INFO"), pytest.raises(HTTPException) as rejected:
        await client.get_file("project", "file")
    assert rejected.value.status_code == status
    assert PRIVATE not in str(rejected.value.detail)
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


async def test_cancellation_is_not_converted_to_a_retryable_gateway_error(
    gateway,
):
    client, calls = gateway(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await client.get_file("project", "file")
    assert len(calls) == 1


async def test_multi_file_upload_keeps_repeated_parts_and_tenant_fields(
    gateway,
):
    client, calls = gateway(body={"files": []})
    files = [
        UploadFile(
            BytesIO(value),
            filename=name,
            headers=Headers({"content-type": "text/plain"}),
        )
        for name, value in [("one.txt", b"first"), ("two.txt", b"second")]
    ]
    await client.upload_files_batch("project", files, "collection", tags="tag")
    request = calls[0]
    assert request.headers["content-type"].startswith("multipart/form-data;")
    assert request.content.count(b'name="files"') == 2
    assert b'filename="one.txt"' in request.content
    assert b'filename="two.txt"' in request.content
    assert b'name="project_id"' in request.content
    assert b'name="collection_id"' in request.content


async def test_json_body_and_billing_service_header_are_preserved(
    gateway, monkeypatch
):
    client, calls = gateway(body={"id": "created"})
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_INTERNAL_TOKEN", SecretStr(PRIVATE))
    body = {"question": "运费谁承担？", "answer": "按已审核政策处理。"}
    assert await client.create_qa_pair("project", "collection", body) == {
        "id": "created",
    }
    assert json.loads(calls[0].content) == body
    assert calls[0].headers["x-saas-service-token"] == PRIVATE
    assert calls[0].headers["x-request-id"]


async def test_download_keeps_binary_bytes_and_headers(gateway):
    client, calls = gateway(raw=b"\xff\x00file", status=200)
    response = await client.download_file("project", "file")
    assert response.content == b"\xff\x00file"
    assert calls[0].url.params["project_id"] == "project"


@pytest.mark.parametrize(
    "kind,status",
    [
        (httpx.ConnectError, 502),
        (httpx.ReadTimeout, 504),
    ],
)
async def test_download_failure_does_not_log_exception_contents(
    gateway,
    caplog,
    kind,
    status,
):
    client, _ = gateway(error=kind(PRIVATE))
    with caplog.at_level("INFO"), pytest.raises(HTTPException) as rejected:
        await client.download_file("project", "file")
    assert rejected.value.status_code == status
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


@pytest.mark.parametrize(
    "total,ready",
    [
        (0, False),
        (2, True),
        (-1, False),
        (None, False),
        ("1", False),
    ],
)
async def test_onboarding_checks_the_collection_count(
    gateway,
    monkeypatch,
    total,
    ready,
):
    from uuid import uuid4
    from app.services import onboarding_service

    client, calls = gateway(body={"data": [], "pagination": {"total": total}})
    monkeypatch.setattr(onboarding_service, "RAGServiceClient", lambda: client)
    project_id = uuid4()
    assert (
        await onboarding_service.check_step_3_rag_collection(project_id)
        is ready
    )
    assert calls[0].url.params["project_id"] == str(project_id)
    assert calls[0].url.params["limit"] == "1"


async def test_onboarding_failure_does_not_log_upstream_error_content(
    gateway,
    monkeypatch,
    caplog,
):
    from uuid import uuid4
    from app.services import onboarding_service

    client, _ = gateway(body={"detail": PRIVATE}, status=403)
    monkeypatch.setattr(onboarding_service, "RAGServiceClient", lambda: client)
    with caplog.at_level("INFO"):
        assert not await onboarding_service.check_step_3_rag_collection(
            uuid4()
        )
    assert PRIVATE not in repr([vars(record) for record in caplog.records])


async def test_onboarding_unexpected_failure_does_not_log_private_content(
    gateway,
    monkeypatch,
    caplog,
):
    from uuid import uuid4
    from app.services import onboarding_service

    client, calls = gateway(error=ValueError(PRIVATE))
    monkeypatch.setattr(onboarding_service, "RAGServiceClient", lambda: client)
    with caplog.at_level("INFO"):
        assert not await onboarding_service.check_step_3_rag_collection(
            uuid4()
        )
    assert len(calls) == 1
    assert PRIVATE not in repr([vars(record) for record in caplog.records])
