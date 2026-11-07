"""Plugin progress keeps SSE frames, tenant scope and safe error messages."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.v1.endpoints import plugins
from app.core.config import settings
from app.schemas.plugin import PluginInstallRequest, PluginUpgradeRequest


PRIVATE = "synthetic-private-plugin-progress-detail"
NORMAL = b'data: {"stage": "download", "message": "working"}\n\n'


class ProgressStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, error=None, wait=False):
        self.chunks = chunks
        self.error = error
        self.wait = wait
        self.closed = False
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.wait:
            self.waiting.set()
            await asyncio.Event().wait()
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


def _transport(
    monkeypatch, stream, *, status=200, error=None,
    content_type="text/event-stream; charset=utf-8",
):
    original = httpx.AsyncClient
    calls, options = [], []

    async def respond(request):
        calls.append(request)
        if error:
            raise error(PRIVATE, request=request)
        return httpx.Response(
            status,
            stream=stream,
            headers={"Content-Type": content_type} if content_type else {},
        )

    def client(**kwargs):
        options.append(kwargs)
        return original(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(settings, "PLUGIN_RUNTIME_URL", "http://runtime.test/")
    return calls, options


async def _progress(kind):
    project_id = uuid4()
    actor = SimpleNamespace(project_id=project_id)
    config = {
        "id": "fixture",
        "name": "Fixture",
        "version": "1.0.0",
        "source": {},
    }
    if kind == "install":
        request = PluginInstallRequest.model_validate(config)
        response = await plugins.install_plugin_stream(request, actor)
    else:
        request = PluginUpgradeRequest.model_validate(
            {
                "latest_config": {
                    **config,
                    "source_url": "https://source.test/plugin",
                },
            }
        )
        response = await plugins.upgrade_plugin_stream(
            "fixture", request, actor
        )
    return response, request, project_id


async def _read(response):
    return b"".join(
        [
            chunk.encode() if isinstance(chunk, str) else chunk
            async for chunk in response.body_iterator
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["install", "upgrade"])
@pytest.mark.parametrize("framing", ["normal", "fields", "crlf"])
async def test_progress_keeps_original_frames_and_owned_request(
    monkeypatch,
    kind,
    framing,
):
    if framing == "normal":
        wire = NORMAL + b'data: {"stage": "complete", "message": "done"}\n\n'
    else:
        wire = (
            ': heartbeat\nevent: progress\nid: receipt-1\nretry: 1000\n'
            'data: {\ndata: "stage": "download",\n'
            'data: "message": "正在下载"}\n\n'
        ).encode()
        if framing == "crlf":
            wire = wire.replace(b"\n", b"\r\n")
    stream = ProgressStream([wire[i:i + 7] for i in range(0, len(wire), 7)])
    calls, _ = _transport(monkeypatch, stream)
    response, request, project = await _progress(kind)
    assert response.media_type == "text/event-stream"
    assert await _read(response) == wire
    assert stream.closed and len(calls) == 1
    call = calls[0]
    assert call.method == "POST"
    expected = request.model_dump()
    if kind == "install":
        expected["project_id"] = str(project)
        assert call.url.path == "/plugins/install-stream"
        assert not call.url.params
    else:
        assert call.url.path == "/plugins/fixture/upgrade"
        assert call.url.params.multi_items() == [("project_id", str(project))]
    assert json.loads(call.content) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["install", "upgrade"])
@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_connection_error_is_safe_and_not_retried(
    monkeypatch,
    caplog,
    kind,
    error,
):
    calls, _ = _transport(monkeypatch, ProgressStream([]), error=error)
    response, _, _ = await _progress(kind)
    wire = (await _read(response)).decode()
    assert json.loads(wire.removeprefix("data:").strip())["stage"] == "error"
    assert PRIVATE not in wire
    records = [
        r
        for r in caplog.records
        if r.name
        in {
            "api.plugins",
            "services.plugin_progress",
        }
    ]
    assert records and all(PRIVATE not in str(r.__dict__) for r in records)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["install", "upgrade"])
@pytest.mark.parametrize("status", [307, 400, 503])
async def test_upstream_failure_is_one_public_event_without_body_or_redirect(
    monkeypatch,
    kind,
    status,
):
    stream = ProgressStream([PRIVATE.encode()])
    calls, _ = _transport(monkeypatch, stream, status=status)
    response, _, _ = await _progress(kind)
    wire = (await _read(response)).decode()
    assert json.loads(wire.removeprefix("data:").strip()) == {
        "stage": "error",
        "message": f"Runtime error: {status}",
    }
    assert PRIVATE not in wire and stream.closed and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["install", "upgrade"])
async def test_interrupted_progress_keeps_prior_frames_and_closes_connection(
    monkeypatch,
    caplog,
    kind,
):
    stream = ProgressStream([NORMAL], error=httpx.ReadError(PRIVATE))
    calls, _ = _transport(monkeypatch, stream)
    response, _, _ = await _progress(kind)
    wire = await _read(response)
    assert wire.startswith(NORMAL)
    last = json.loads(
        wire[len(NORMAL):].decode().removeprefix("data:").strip()
    )
    assert last["stage"] == "error" and PRIVATE not in last["message"]
    assert PRIVATE not in caplog.text
    assert stream.closed and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["install", "upgrade"])
async def test_cancelled_consumer_closes_upstream_without_another_request(
    monkeypatch,
    kind,
):
    stream = ProgressStream([NORMAL], wait=True)
    calls, _ = _transport(monkeypatch, stream)
    response, _, _ = await _progress(kind)
    iterator = response.body_iterator
    await anext(iterator)
    pending = asyncio.create_task(anext(iterator))
    await asyncio.wait_for(stream.waiting.wait(), 2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await iterator.aclose()
    assert stream.closed and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["install", "upgrade"])
async def test_stream_has_bounded_connection_and_unbounded_progress_wait(
    monkeypatch,
    kind,
):
    _, options = _transport(monkeypatch, ProgressStream([NORMAL]))
    response, _, _ = await _progress(kind)
    await _read(response)
    timeout = options[0]["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read is None
    assert timeout.connect == settings.PLUGIN_RUNTIME_TIMEOUT
    assert timeout.write == settings.PLUGIN_RUNTIME_TIMEOUT
    assert timeout.pool == settings.PLUGIN_RUNTIME_TIMEOUT


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["install", "upgrade"])
@pytest.mark.parametrize(
    "content_type", [None, "application/json", "text/html"]
)
async def test_non_progress_response_is_not_forwarded_as_a_successful_stream(
    monkeypatch, kind, content_type,
):
    stream = ProgressStream([PRIVATE.encode()])
    calls, _ = _transport(monkeypatch, stream, content_type=content_type)
    response, _, _ = await _progress(kind)
    wire = await _read(response)
    assert wire.startswith(b"data: ")
    assert PRIVATE.encode() not in wire
    event = json.loads(wire.decode().removeprefix("data:").strip())
    assert event["stage"] == "error"
    assert stream.closed and len(calls) == 1
