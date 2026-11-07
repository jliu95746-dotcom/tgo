"""Configuration failures remain bounded and do not expose private details."""

import html
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4
from datetime import UTC, datetime

import httpx
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI, HTTPException, UploadFile
from starlette.datastructures import Headers
import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from app.api.v1.endpoints import docs, platforms
from app.core.config import Settings, load_settings
from app.schemas.provider_setup import ProviderConnectionDraft
from app.services import provider_setup, wechat_local_bridge
from app.services.wecom_app_notifications import pending_assist_notifications
from app.utils.crypto import decrypt_str, encrypt_str
from app.utils.encoding import encode_channel_id

PRIVATE = "SYNTHETIC-CONFIGURATION-PRIVATE"


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)


def documentation_app():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(docs.router)
    return app


def install_upstream(monkeypatch, *, response=None, error=None):
    monkeypatch.setitem(
        docs.SERVICES,
        "ai",
        {
            "name": "AI Service",
            "url": "https://docs.example.test",
            "openapi_path": "/openapi.json",
        },
    )
    getter = AsyncMock(return_value=response, side_effect=error)
    manager = MagicMock()
    manager.__aenter__.return_value = SimpleNamespace(get=getter)
    factory = Mock(return_value=manager)
    monkeypatch.setattr(docs.httpx, "AsyncClient", factory)
    return factory, getter


@pytest.mark.parametrize(
    "payload", ["<svg onload=alert(1)>", "<img src=x onerror=alert(1)>"]
)
def test_error_html_displays_text_without_creating_injected_elements(payload):
    body = docs._error_html(payload, payload)
    parser = Tags()
    parser.feed(body)
    assert not {"svg", "img", "script"}.intersection(parser.tags)
    assert payload not in body
    assert payload in html.unescape(body)


@pytest.mark.asyncio
async def test_unknown_documentation_service_is_escaped_and_stays_404():
    payload = "<svg onload=alert(1)>"
    async with AsyncClient(
        transport=ASGITransport(app=documentation_app()),
        base_url="http://testserver",
    ) as client:
        result = await client.get("/docs/" + payload)
    assert result.status_code == 404
    assert "<svg" not in result.text
    assert payload in html.unescape(result.text)


@pytest.mark.asyncio
async def test_proxy_failure_hides_exception_details_and_traceback(
    monkeypatch, caplog
):
    factory, getter = install_upstream(
        monkeypatch, error=RuntimeError(PRIVATE)
    )
    with pytest.raises(HTTPException) as captured:
        await docs.proxy_openapi_json("ai")
    assert captured.value.status_code == 503
    assert PRIVATE not in caplog.text
    assert PRIVATE not in str(captured.value.detail)
    assert captured.value.__suppress_context__
    factory.assert_called_once()
    getter.assert_awaited_once_with("https://docs.example.test/openapi.json")


@pytest.mark.asyncio
async def test_missing_remote_documentation_url_stops_before_http(monkeypatch):
    factory, _ = install_upstream(monkeypatch)
    monkeypatch.setitem(docs.SERVICES["ai"], "url", None)
    with pytest.raises(HTTPException) as captured:
        await docs.proxy_openapi_json("ai")
    assert captured.value.status_code == 503
    factory.assert_not_called()


@pytest.mark.parametrize("payload", [{"paths": {}}, [{"value": 2}], "ready"])
@pytest.mark.asyncio
async def test_valid_proxy_json_keeps_its_payload_and_status(
    payload, monkeypatch
):
    response = httpx.Response(
        200,
        json=payload,
        request=httpx.Request("GET", "https://docs.example.test/openapi.json"),
    )
    install_upstream(monkeypatch, response=response)
    async with AsyncClient(
        transport=ASGITransport(app=documentation_app()),
        base_url="http://testserver",
    ) as client:
        result = await client.get("/docs/ai/openapi.json")
    assert result.status_code == 200
    assert result.json() == payload


@pytest.mark.asyncio
async def test_non_finite_proxy_json_is_a_bounded_service_failure(monkeypatch):
    response = httpx.Response(
        200,
        content=b'{"value":NaN}',
        request=httpx.Request("GET", "https://docs.example.test/openapi.json"),
    )
    install_upstream(monkeypatch, response=response)
    with pytest.raises(HTTPException) as captured:
        await docs.proxy_openapi_json("ai")
    assert captured.value.status_code == 503


@pytest.mark.parametrize("provider", ["ollama", "deepseek"])
def test_unusable_stored_provider_key_never_becomes_a_non_string_connection(
    provider, monkeypatch
):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = (
        SimpleNamespace(
            api_base_url="https://model.example.test/v1",
            provider=provider,
            api_key=PRIVATE,
        )
    )
    monkeypatch.setattr(provider_setup, "decrypt_str", lambda value: None)
    draft = ProviderConnectionDraft(
        provider_id=uuid4(),
        provider=provider,
        api_base_url="https://model.example.test/v1",
    )
    if provider == "ollama":
        assert provider_setup.resolve_connection(db, uuid4(), draft).key == ""
    else:
        with pytest.raises(HTTPException) as captured:
            provider_setup.resolve_connection(db, uuid4(), draft)
        assert captured.value.status_code == 422
        assert PRIVATE not in str(captured.value.detail)


@pytest.mark.parametrize("failure", ["mkdir", "write"])
@pytest.mark.asyncio
async def test_platform_logo_storage_failure_has_no_private_detail(
    tmp_path, monkeypatch, caplog, failure
):
    platform_id, project_id = uuid4(), uuid4()
    monkeypatch.setattr(
        platforms.settings, "PLATFORM_LOGO_UPLOAD_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        platforms.settings, "PLATFORM_LOGO_ALLOWED_TYPES", ["image/png"]
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = (
        SimpleNamespace(id=platform_id, project_id=project_id)
    )
    if failure == "write":
        monkeypatch.setattr(
            platforms,
            "open",
            Mock(side_effect=OSError(PRIVATE)),
            raising=False,
        )
    else:
        original = Path.mkdir

        def mkdir(path, *args, **kwargs):
            if path == tmp_path / str(platform_id):
                raise OSError(PRIVATE)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", mkdir)
    upload = UploadFile(
        BytesIO(b"synthetic-image"),
        filename="logo.png",
        headers=Headers({"content-type": "image/png"}),
    )
    with pytest.raises(HTTPException) as captured:
        await platforms.upload_platform_logo(
            platform_id,
            upload,
            db,
            SimpleNamespace(project_id=project_id),
            "zh",
        )
    assert captured.value.status_code == 500
    assert PRIVATE not in str(captured.value.detail)
    assert PRIVATE not in caplog.text
    assert captured.value.__suppress_context__
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_logo_read_uses_one_path_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(
        platforms.settings, "PLATFORM_LOGO_UPLOAD_DIR", str(tmp_path)
    )
    target = tmp_path / "logo.png"
    target.write_bytes(b"synthetic-image")

    class ChangingLogo:
        reads = 0

        @property
        def logo_path(self):
            self.reads += 1
            return "logo.png" if self.reads == 1 else None

    platform = ChangingLogo()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = platform
    result = await platforms.get_platform_logo(uuid4(), db)
    assert Path(result.path) == target
    assert result.headers["cache-control"] == "public, max-age=86400"
    assert platform.reads == 1


@pytest.mark.asyncio
async def test_missing_logo_still_returns_404(tmp_path, monkeypatch):
    monkeypatch.setattr(
        platforms.settings, "PLATFORM_LOGO_UPLOAD_DIR", str(tmp_path)
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = (
        SimpleNamespace(logo_path=None)
    )
    with pytest.raises(HTTPException) as captured:
        await platforms.get_platform_logo(uuid4(), db)
    assert captured.value.status_code == 404


@pytest.mark.parametrize("operation", ["issue", "exchange"])
@pytest.mark.asyncio
async def test_pairing_does_not_use_a_missing_redis_url_after_authorization(
    operation, monkeypatch
):
    service = wechat_local_bridge
    monkeypatch.setattr(service.settings, "WECHAT_LOCAL_SCOPED_ENABLED", True)
    monkeypatch.setattr(
        service.settings, "REDIS_URL", "redis://cache.example.test:6379"
    )
    original = service._pairing_key

    def key(*args):
        result = original(*args)
        monkeypatch.setattr(service.settings, "REDIS_URL", None)
        return result

    monkeypatch.setattr(service, "_pairing_key", key)
    factory = Mock(side_effect=AssertionError(PRIVATE))
    monkeypatch.setattr(service.Redis, "from_url", factory)
    platform = SimpleNamespace(id=uuid4(), api_key=PRIVATE, is_active=True)
    with pytest.raises(HTTPException) as captured:
        if operation == "issue":
            await service.issue_pairing_code(platform, "a" * 64)
        else:
            await service.exchange_pairing_code(platform, "code", "verifier")
    assert captured.value.status_code == 503
    factory.assert_not_called()


def test_notification_with_no_message_id_or_timestamp_is_skipped(monkeypatch):
    from app.services import chat_service

    monkeypatch.setattr(
        chat_service,
        "resolve_service_mode",
        lambda platform, visitor: "assist",
    )
    now = datetime.now(UTC)
    platform = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        config={
            "app_notification_enabled": True,
            "app_notification_enabled_at": now.isoformat(),
        },
    )
    rows = [
        SimpleNamespace(id=uuid4(), last_client_msg_no="existing"),
        SimpleNamespace(
            id=uuid4(),
            last_client_msg_no=None,
            last_message_at=None,
            last_message_seq=1,
        ),
        SimpleNamespace(
            id=uuid4(),
            last_client_msg_no=None,
            last_message_at=now,
            last_message_seq=17,
        ),
    ]
    db = MagicMock()
    query = db.query.return_value.filter.return_value
    query.order_by.return_value.limit.return_value.all.return_value = rows
    result = pending_assist_notifications(db, platform)
    assert [item.event_id for item in result] == [
        "existing",
        f"17:{now.isoformat()}",
    ]


def test_null_utility_inputs_keep_existing_compatibility():
    assert encrypt_str(None) == ""
    assert encode_channel_id(None) == "0"
    assert (
        decrypt_str(encrypt_str("configuration-value"))
        == "configuration-value"
    )


@pytest.mark.parametrize("path", ["/docs", "/docs/api", "/docs/ai"])
@pytest.mark.asyncio
async def test_documentation_pages_keep_html_response(path):
    async with AsyncClient(
        transport=ASGITransport(app=documentation_app()),
        base_url="http://testserver",
    ) as client:
        response = await client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<!DOCTYPE html>" in response.text
    if path == "/docs":
        assert 'href="/v1/docs/ai"' in response.text
    elif path == "/docs/api":
        assert 'url: "/v1/openapi.json"' in response.text
    else:
        assert 'url: "/v1/docs/ai/openapi.json"' in response.text


@pytest.mark.parametrize("configured", [False, True])
def test_settings_factory_keeps_required_environment_fields(
    monkeypatch, configured
):
    class EnvironmentSettings(Settings):
        model_config = SettingsConfigDict(env_file=None)

    assert EnvironmentSettings.model_fields["SECRET_KEY"].is_required()
    assert EnvironmentSettings.model_fields["DATABASE_URL"].is_required()
    for name in ["SECRET_KEY", "DATABASE_URL"]:
        monkeypatch.delenv(name, raising=False)
    if not configured:
        with pytest.raises(ValidationError) as captured:
            load_settings(EnvironmentSettings)
        missing = {
            error["loc"]
            for error in captured.value.errors()
            if error["type"] == "missing"
        }
        assert {("SECRET_KEY",), ("DATABASE_URL",)} <= missing
    else:
        secret = PRIVATE + "-KEY"
        monkeypatch.setenv("SECRET_KEY", secret)
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql://synthetic:synthetic@127.0.0.1:1/test"
        )
        result = load_settings(EnvironmentSettings)
        assert isinstance(result, EnvironmentSettings)
        assert result.SECRET_KEY == secret
        assert str(result.DATABASE_URL) == (
            "postgresql://synthetic:synthetic@127.0.0.1:1/test"
        )


@pytest.mark.asyncio
async def test_logo_upload_stores_new_file_and_replaces_old_file(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        platforms.settings, "PLATFORM_LOGO_UPLOAD_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        platforms.settings, "PLATFORM_LOGO_ALLOWED_TYPES", ["image/png"]
    )
    monkeypatch.setattr(platforms.settings, "PLATFORM_LOGO_MAX_SIZE_MB", 1)
    previous = tmp_path / "old.png"
    previous.write_bytes(b"old-image")
    platform = SimpleNamespace(
        id=uuid4(), project_id=uuid4(), logo_path=previous.name
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = platform
    response = Mock()
    build = Mock(return_value=response)
    monkeypatch.setattr(platforms, "_build_platform_response", build)
    payload = b"synthetic-image"
    upload = UploadFile(
        BytesIO(payload),
        filename="logo.png",
        headers=Headers({"content-type": "image/png"}),
    )
    result = await platforms.upload_platform_logo(
        platform.id,
        upload,
        db,
        SimpleNamespace(project_id=platform.project_id),
        "zh",
    )
    assert result is response
    assert (tmp_path / platform.logo_path).read_bytes() == payload
    assert not previous.exists()
    db.commit.assert_called_once_with()
    db.refresh.assert_called_once_with(platform)
    build.assert_called_once_with(platform, language="zh")


@pytest.mark.parametrize(
    "reason,expected", [("scope", 403), ("mime", 400), ("size", 413)]
)
@pytest.mark.asyncio
async def test_logo_upload_preserves_validation_and_does_not_commit(
    tmp_path, monkeypatch, reason, expected
):
    monkeypatch.setattr(
        platforms.settings, "PLATFORM_LOGO_UPLOAD_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        platforms.settings, "PLATFORM_LOGO_ALLOWED_TYPES", ["image/png"]
    )
    monkeypatch.setattr(platforms.settings, "PLATFORM_LOGO_MAX_SIZE_MB", 0)
    platform = SimpleNamespace(id=uuid4(), project_id=uuid4(), logo_path=None)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = platform
    mime = "text/plain" if reason == "mime" else "image/png"
    upload = UploadFile(
        BytesIO(b"synthetic-image"),
        filename="logo.png",
        headers=Headers({"content-type": mime}),
    )
    project = uuid4() if reason == "scope" else platform.project_id
    with pytest.raises(HTTPException) as captured:
        await platforms.upload_platform_logo(
            platform.id, upload, db, SimpleNamespace(project_id=project), "zh"
        )
    assert captured.value.status_code == expected
    assert not list(tmp_path.rglob("*.png"))
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_logo_outside_upload_root_is_not_served(tmp_path, monkeypatch):
    base = tmp_path / "logos"
    base.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"synthetic-image")
    monkeypatch.setattr(
        platforms.settings, "PLATFORM_LOGO_UPLOAD_DIR", str(base)
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = (
        SimpleNamespace(logo_path="../outside.png")
    )
    with pytest.raises(HTTPException) as captured:
        await platforms.get_platform_logo(uuid4(), db)
    assert captured.value.status_code == 500
    assert outside.read_bytes() == b"synthetic-image"
