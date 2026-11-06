"""Exercise tenant-scoped RAG contracts over real loopback HTTP sockets."""

from collections import deque
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from threading import Thread
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from pydantic import SecretStr
import pytest
from starlette.datastructures import Headers

from app.core.config import settings
from app.services.rag_client import RAGServiceClient

pytestmark = pytest.mark.asyncio
PROJECT = "synthetic-tenant"
PRIVATE = "synthetic-sensitive-rag-content"


@pytest.fixture
def peer(monkeypatch):
    """Own an ephemeral socket; never use a live provider or database."""
    requests = []
    responses = deque()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            content = self.rfile.read(
                int(self.headers.get("Content-Length", 0))
            )
            requests.append((self.command, self.path, self.headers, content))
            status, body, headers = responses.popleft()
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = RAGServiceClient()
    client.base_url = f"http://127.0.0.1:{server.server_port}"
    client.timeout = 2

    class Peer:
        def reply(self, body=None, *, status=200, raw=None, headers=None):
            content = json.dumps(body).encode() if raw is None else raw
            responses.append(
                (
                    status,
                    content,
                    headers
                    or {
                        "Content-Type": "application/json",
                    },
                )
            )

        def check(self, method, path, *, query=None, body=None):
            verb, target, headers, content = requests[-1]
            parsed = urlsplit(target)
            assert (verb, parsed.path) == (method, path)
            assert parse_qs(parsed.query) == {
                "project_id": [PROJECT],
                **{key: [str(value)] for key, value in (query or {}).items()},
            }
            assert headers["X-Request-ID"]
            if body is None:
                assert content == b""
            else:
                assert json.loads(content) == body

    result = Peer()
    result.client = client
    result.requests = requests
    try:
        yield result
        assert not responses, "Every planned HTTP response must be consumed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


async def test_collection_and_file_queries_keep_tenant_and_filters(peer):
    client = peer.client
    data = {"display_name": "售后资料", "collection_type": "file"}
    peer.reply({"id": "collection"})
    assert await client.create_collection(PROJECT, data) == {
        "id": "collection"
    }
    peer.check("POST", "/v1/collections", body=data)
    peer.reply({"id": "collection", **data})
    assert await client.update_collection(PROJECT, "collection", data) == {
        "id": "collection",
        **data,
    }
    peer.check("PUT", "/v1/collections/collection", body=data)
    peer.reply({"id": "collection"})
    await client.get_collection(PROJECT, "collection", include_stats=True)
    peer.check(
        "GET", "/v1/collections/collection", query={"include_stats": "true"}
    )
    peer.reply({"data": [], "pagination": {"has_previous": True}})
    result = await client.list_collections(
        PROJECT,
        display_name="售后",
        collection_type="file",
        tags="政策",
        limit=3,
        offset=2,
    )
    assert result["pagination"] == {"has_prev": True}
    peer.check(
        "GET",
        "/v1/collections",
        query={
            "display_name": "售后",
            "collection_type": "file",
            "tags": "政策",
            "limit": 3,
            "offset": 2,
        },
    )
    peer.reply({"data": [], "pagination": {"has_previous": False}})
    assert (
        await client.list_files(
            PROJECT,
            collection_id="collection",
            status="completed",
            content_type="text/plain",
            uploaded_by="staff",
            tags="policy",
            limit=2,
            offset=1,
        )
    )["pagination"] == {"has_prev": False}
    peer.check(
        "GET",
        "/v1/files",
        query={
            "collection_id": "collection",
            "status": "completed",
            "content_type": "text/plain",
            "uploaded_by": "staff",
            "tags": "policy",
            "limit": 2,
            "offset": 1,
        },
    )
    peer.reply({"data": [{"id": "chunk"}]})
    assert (
        await client.list_file_documents(
            PROJECT,
            "file",
            content_type="text",
            chunk_index=0,
        )
    )["data"] == [{"id": "chunk"}]
    peer.check(
        "GET",
        "/v1/files/file/documents",
        query={
            "content_type": "text",
            "chunk_index": 0,
            "limit": 20,
            "offset": 0,
        },
    )
    peer.reply({"id": "file"})
    assert await client.get_file(PROJECT, "file") == {"id": "file"}
    peer.check("GET", "/v1/files/file")
    search = {"query": "签收后几天退货？", "limit": 2}
    peer.reply({"data": [{"content": "已审核政策"}]})
    await client.search_collection_documents(PROJECT, "collection", search)
    peer.check(
        "POST", "/v1/collections/collection/documents/search", body=search
    )


async def test_governance_and_version_calls_keep_channel_and_review_actor(
    peer,
):
    client = peer.client
    project_id = str(uuid4())
    peer.reply({"project_id": project_id, "channel": "web", "collections": []})
    availability = await client.knowledge_availability(project_id, "web")
    assert str(availability.project_id) == project_id
    assert availability.channel == "web"
    assert parse_qs(urlsplit(peer.requests[-1][1]).query) == {
        "project_id": [project_id],
        "channel": ["web"],
    }
    draft = {"channels": ["web"], "allow_automatic_reply": False}
    review = {"decision": "approve", "actor": "authenticated-admin"}
    operations = [
        (
            "update_knowledge_channels",
            {"record_id": "record", "data": draft},
            "PATCH",
            "/v1/knowledge-governance/record/channels",
            draft,
            {},
        ),
        (
            "list_knowledge_governance",
            {"collection_id": "collection", "review_status": "pending"},
            "GET",
            "/v1/knowledge-governance",
            None,
            {
                "collection_id": "collection",
                "review_status": "pending",
                "limit": 50,
                "offset": 0,
            },
        ),
        (
            "save_file_knowledge_governance",
            {"file_id": "file", "data": draft},
            "PUT",
            "/v1/knowledge-governance/files/file",
            draft,
            {},
        ),
        (
            "save_qa_knowledge_governance",
            {"qa_pair_id": "qa", "data": draft},
            "PUT",
            "/v1/knowledge-governance/qa-pairs/qa",
            draft,
            {},
        ),
        (
            "submit_knowledge_governance",
            {"record_id": "record"},
            "POST",
            "/v1/knowledge-governance/record/submit",
            None,
            {},
        ),
        (
            "review_knowledge_governance",
            {"record_id": "record", "decision": review},
            "POST",
            "/v1/knowledge-governance/record/review",
            review,
            {},
        ),
        (
            "backfill_knowledge_governance",
            {"data": {"dry_run": True}},
            "POST",
            "/v1/knowledge-governance/backfill",
            {"dry_run": True},
            {},
        ),
        (
            "knowledge_version_request",
            {"method": "GET", "path": "/file/file"},
            "GET",
            "/v1/knowledge-versions/file/file",
            None,
            {},
        ),
    ]
    for name, arguments, verb, path, body, query in operations:
        peer.reply({"id": "record", "status": "synthetic"})
        assert await getattr(client, name)(
            project_id=PROJECT, **arguments
        ) == {
            "id": "record",
            "status": "synthetic",
        }
        peer.check(verb, path, body=body, query=query)


async def test_qa_management_keeps_bodies_and_pagination(peer):
    client = peer.client
    qa = {"question": "多久可退货？", "answer": "按已审核政策处理"}
    operations = [
        (
            "create_qa_pair",
            ("collection", qa),
            {},
            "POST",
            "/v1/collections/collection/qa-pairs",
            qa,
            {},
        ),
        (
            "list_qa_pairs",
            ("collection",),
            {"category": "售后", "status": "active", "limit": 3, "offset": 1},
            "GET",
            "/v1/collections/collection/qa-pairs",
            None,
            {"category": "售后", "status": "active", "limit": 3, "offset": 1},
        ),
        (
            "batch_create_qa_pairs",
            ("collection", {"items": [qa]}),
            {},
            "POST",
            "/v1/collections/collection/qa-pairs/batch",
            {"items": [qa]},
            {},
        ),
        (
            "import_qa_pairs",
            ("collection", {"content": "question,answer"}),
            {},
            "POST",
            "/v1/collections/collection/qa-pairs/import",
            {"content": "question,answer"},
            {},
        ),
        ("get_qa_pair", ("qa",), {}, "GET", "/v1/qa-pairs/qa", None, {}),
        ("update_qa_pair", ("qa", qa), {}, "PUT", "/v1/qa-pairs/qa", qa, {}),
        (
            "list_qa_categories",
            (),
            {"collection_id": "collection"},
            "GET",
            "/v1/qa-categories",
            None,
            {"collection_id": "collection"},
        ),
    ]
    for name, arguments, options, verb, path, body, query in operations:
        peer.reply({"data": [], "pagination": {"total": 0}})
        assert await getattr(client, name)(PROJECT, *arguments, **options) == {
            "data": [],
            "pagination": {"total": 0},
        }
        peer.check(verb, path, body=body, query=query)


async def test_website_management_keeps_crawl_options(peer):
    client = peer.client
    page = {"url": "https://example.invalid/policy"}
    operations = [
        (
            "list_collection_pages",
            ("collection",),
            {"status": "completed"},
            "GET",
            "/v1/collections/collection/pages",
            None,
            {"status": "completed", "limit": 20, "offset": 0},
        ),
        (
            "list_website_pages",
            ("collection",),
            {
                "status": "pending",
                "depth": 0,
                "parent_page_id": "parent",
                "tree_depth": -1,
            },
            "GET",
            "/v1/websites/pages",
            None,
            {
                "collection_id": "collection",
                "status": "pending",
                "depth": 0,
                "parent_page_id": "parent",
                "tree_depth": -1,
                "limit": 20,
                "offset": 0,
            },
        ),
        (
            "get_website_page",
            ("page",),
            {},
            "GET",
            "/v1/websites/pages/page",
            None,
            {},
        ),
        (
            "add_website_page",
            ("collection", page),
            {},
            "POST",
            "/v1/websites/pages",
            page,
            {"collection_id": "collection"},
        ),
        (
            "recrawl_website_page",
            ("page",),
            {},
            "POST",
            "/v1/websites/pages/page/recrawl",
            None,
            {},
        ),
        (
            "crawl_deeper_from_page",
            ("page", {"max_depth": 2}),
            {},
            "POST",
            "/v1/websites/pages/page/crawl-deeper",
            {"max_depth": 2},
            {},
        ),
        (
            "get_crawl_progress",
            ("collection",),
            {},
            "GET",
            "/v1/websites/progress",
            None,
            {"collection_id": "collection"},
        ),
    ]
    for name, arguments, options, verb, path, body, query in operations:
        peer.reply({"data": [], "pagination": {"has_previous": False}})
        result = await getattr(client, name)(PROJECT, *arguments, **options)
        assert result["data"] == []
        peer.check(verb, path, body=body, query=query)


@pytest.mark.parametrize("batch", [False, True])
async def test_file_uploads_preserve_content_and_multipart_fields(peer, batch):
    client = peer.client
    uploads = [
        UploadFile(
            BytesIO(content),
            filename=name,
            headers=Headers({"content-type": "text/plain"}),
        )
        for name, content in [("one.txt", b"first"), ("two.txt", b"second")]
    ]
    peer.reply({"files": []})
    if batch:
        await client.upload_files_batch(
            PROJECT,
            uploads,
            "collection",
            "description",
            "zh",
            "policy",
        )
    else:
        await client.upload_file(
            PROJECT,
            uploads[0],
            "collection",
            "description",
            "zh",
            "policy",
        )
    verb, target, headers, body = peer.requests[-1]
    assert (verb, target) == (
        "POST",
        "/v1/files/batch" if batch else "/v1/files",
    )
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {headers['Content-Type']}\r\n\r\n".encode() + body,
    )
    parts = list(message.iter_parts())
    files = [part for part in parts if part.get_filename()]
    assert [part.get_filename() for part in files] == (
        ["one.txt", "two.txt"] if batch else ["one.txt"]
    )
    assert [part.get_payload(decode=True) for part in files] == (
        [b"first", b"second"] if batch else [b"first"]
    )
    assert {
        part.get_param("name", header="content-disposition"): part.get_payload(
            decode=True
        ).decode()
        for part in parts
        if not part.get_filename()
    } == {
        "project_id": PROJECT,
        "collection_id": "collection",
        "description": "description",
        "language": "zh",
        "tags": "policy",
    }


async def test_replacement_upload_and_billing_header(peer, monkeypatch):
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_INTERNAL_TOKEN", SecretStr(PRIVATE))
    peer.reply({"version": 2})
    upload = UploadFile(BytesIO(b"replacement"), filename="policy.txt")
    assert await peer.client.upload_knowledge_replacement(
        PROJECT,
        "reference",
        "authenticated-staff",
        upload,
    ) == {"version": 2}
    _, target, headers, body = peer.requests[-1]
    assert (
        urlsplit(target).path == "/v1/knowledge-versions/file/reference/upload"
    )
    assert parse_qs(urlsplit(target).query) == {"project_id": [PROJECT]}
    assert headers["X-SaaS-Service-Token"] == PRIVATE
    assert b'name="actor"' in body
    assert b"authenticated-staff" in body
    assert b"replacement" in body


@pytest.mark.parametrize(
    "name,path",
    [
        ("delete_collection", "/v1/collections/resource"),
        ("delete_file", "/v1/files/resource"),
        ("delete_website_page", "/v1/websites/pages/resource"),
        ("delete_qa_pair", "/v1/qa-pairs/resource"),
    ],
)
@pytest.mark.parametrize("status", [200, 204])
async def test_deletes_return_no_body_and_keep_scope(peer, name, path, status):
    peer.reply(
        {"deleted": True} if status == 200 else None,
        status=status,
        raw=b"" if status == 204 else None,
    )
    assert await getattr(peer.client, name)(PROJECT, "resource") is None
    peer.check("DELETE", path)


@pytest.mark.parametrize("status", [200, 404])
async def test_download_preserves_raw_status_bytes_and_headers(peer, status):
    peer.reply(
        status=status,
        raw=b"\xff\x00bytes",
        headers={
            "Content-Type": "application/octet-stream",
            "X-Test-File": "version-1",
        },
    )
    response = await peer.client.download_file(PROJECT, "file")
    assert response.status_code == status
    assert response.content == b"\xff\x00bytes"
    assert response.headers["X-Test-File"] == "version-1"
    peer.check("GET", "/v1/files/file/download")


@pytest.mark.parametrize(
    "body", [[], None, "not an object", {"pagination": []}]
)
async def test_malformed_success_fails_once_with_controlled_error(peer, body):
    peer.reply(body)
    with pytest.raises(HTTPException) as rejected:
        await peer.client.list_collections(PROJECT)
    assert rejected.value.status_code == 502
    assert len(peer.requests) == 1


@pytest.mark.parametrize("status", [400, 403, 404, 409, 429, 503])
async def test_error_status_and_detail_survive_without_private_logs(
    peer,
    caplog,
    status,
):
    body = {"error": {"message": PRIVATE}}
    peer.reply(body, status=status)
    with caplog.at_level("INFO"), pytest.raises(HTTPException) as rejected:
        await peer.client.list_collections(PROJECT, display_name=PRIVATE)
    assert rejected.value.status_code == status
    assert rejected.value.detail == body
    assert len(peer.requests) == 1
    assert PRIVATE not in repr([vars(record) for record in caplog.records])
