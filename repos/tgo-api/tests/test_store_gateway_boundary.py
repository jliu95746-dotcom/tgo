"""Store binding cannot persist malformed data or expose upstream failures."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.api.v1.endpoints import store
from app.models import AIModel, AIProvider, Project
from app.models.store_credential import StoreCredential
from app.services import store_sync
from app.utils.crypto import decrypt_str, encrypt_str

PRIVATE = "synthetic-private-store-gateway"
KEY = {
    "user_id": "store-user",
    "email": "store@example.invalid",
    "api_key": PRIVATE,
}


@compiles(JSONB, "sqlite")
def compile_jsonb(_type, compiler, **kwargs):
    return "JSON"


@pytest.fixture
def records():
    engine = create_engine("sqlite://")
    for model in (Project, StoreCredential, AIProvider, AIModel):
        model.__table__.create(engine)
    with Session(engine) as db:
        projects = [
            Project(name=name, api_key=name) for name in ("store-A", "store-B")
        ]
        db.add_all(projects)
        db.commit()
        yield db, projects
    engine.dispose()


@pytest.fixture
def upstream(monkeypatch):
    original = httpx.AsyncClient
    state = SimpleNamespace(
        response=httpx.Response(200, json=KEY), error=None, requests=[]
    )

    def handle(request):
        state.requests.append(request)
        if state.error is not None:
            raise state.error
        return state.response

    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return state


@pytest.mark.asyncio
async def test_binding_stores_encrypted_key_and_preserves_other_project(
    records, upstream
):
    db, projects = records
    other = StoreCredential(
        project_id=projects[1].id,
        store_user_id="other",
        store_email="other@example.invalid",
        api_key_encrypted=encrypt_str("other-synthetic-key"),
    )
    db.add(other)
    db.commit()
    original_ciphertext = other.api_key_encrypted
    user = SimpleNamespace(project_id=projects[0].id)
    first = await store.bind_store(
        store.StoreBindRequest(access_token=PRIVATE), db, user
    )
    assert first.project_id == projects[0].id
    assert first.api_key_encrypted != PRIVATE
    assert decrypt_str(first.api_key_encrypted) == PRIVATE
    assert first.store_user_id == KEY["user_id"]
    assert first.store_email == KEY["email"]
    public = store.StoreCredentialSchema.model_validate(
        first
    ).model_dump_json()
    assert PRIVATE not in public and "api_key" not in public
    upstream.response = httpx.Response(
        200, json={**KEY, "api_key": "replacement-key"}
    )
    second = await store.bind_store(
        store.StoreBindRequest(access_token=PRIVATE), db, user
    )
    assert first.id == second.id
    assert decrypt_str(second.api_key_encrypted) == "replacement-key"
    db.refresh(other)
    assert other.api_key_encrypted == original_ciphertext
    assert len(db.scalars(select(StoreCredential)).all()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(
    "body", [{}, {**KEY, "api_key": None}, {**KEY, "email": " "}, []]
)
async def test_invalid_binding_response_never_changes_saved_credentials(
    records, upstream, existing, body
):
    db, projects = records
    if existing:
        credential = StoreCredential(
            project_id=projects[0].id,
            store_user_id="previous-user",
            store_email="previous@example.invalid",
            api_key_encrypted=encrypt_str("previous-key"),
        )
        db.add(credential)
        db.commit()
        original_ciphertext = credential.api_key_encrypted
    upstream.response = httpx.Response(200, json=body)
    with pytest.raises(HTTPException) as failure:
        await store.bind_store(
            store.StoreBindRequest(access_token=PRIVATE),
            db,
            SimpleNamespace(project_id=projects[0].id),
        )
    assert failure.value.status_code == 502
    assert PRIVATE not in str(failure.value.detail)
    saved = db.scalar(
        select(StoreCredential).where(
            StoreCredential.project_id == projects[0].id
        )
    )
    if existing:
        db.refresh(saved)
        assert saved.api_key_encrypted == original_ciphertext
        assert saved.store_user_id == "previous-user"
        assert saved.store_email == "previous@example.invalid"
    else:
        assert saved is None
    assert not db.new and not db.dirty


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["bind", "install"])
async def test_fetch_failures_have_static_public_details(
    monkeypatch, target, caplog
):
    failure = AsyncMock(side_effect=RuntimeError(PRIVATE))
    db = MagicMock()
    if target == "bind":
        monkeypatch.setattr(store.store_client, "get_api_key", failure)
        operation = store.bind_store(
            store.StoreBindRequest(access_token=PRIVATE),
            db,
            SimpleNamespace(project_id=uuid4()),
        )
    else:
        monkeypatch.setattr(store.store_client, "get_model", failure)
        operation = store._install_model_internal(
            "model-resource", uuid4(), PRIVATE, db
        )
    with pytest.raises(HTTPException) as error:
        await operation
    assert error.value.status_code == 502
    assert PRIVATE not in str(error.value.detail)
    assert PRIVATE not in caplog.text
    db.commit.assert_not_called()
    db.add.assert_not_called()


@pytest.fixture
def model_store(monkeypatch):
    from app.schemas.store import StoreModelDetail

    detail = StoreModelDetail.model_validate(
        {
            "id": "model-resource",
            "name": "model-name",
            "model_type": "chat",
            "input_price": 0,
            "output_price": 0,
            "provider": {
                "id": "provider-resource",
                "name": "Provider",
                "slug": "p",
            },
        }
    )
    monkeypatch.setattr(
        store.store_client, "get_model", AsyncMock(return_value=detail)
    )
    monkeypatch.setattr(
        store.store_client, "install_model", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        store.store_client, "uninstall_model", AsyncMock(return_value={})
    )
    sync = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(store, "sync_provider_with_retry_and_update", sync)
    from app.services import shared_models

    monkeypatch.setattr(
        shared_models, "shared_models_active", lambda db: False
    )
    return sync


@pytest.mark.asyncio
@pytest.mark.parametrize("recording_failure", [False, True])
async def test_install_update_stays_scoped_and_recording_remains_best_effort(
    records, model_store, monkeypatch, recording_failure, caplog
):
    db, projects = records
    if recording_failure:
        monkeypatch.setattr(
            store.store_client,
            "install_model",
            AsyncMock(side_effect=RuntimeError(PRIVATE)),
        )
    for index in (0, 0, 1):
        result = await store._install_model_internal(
            "model-resource", projects[index].id, PRIVATE, db
        )
        assert result == {
            "success": True,
            "model_id": "model-name",
            "provider": "Store-Provider",
        }
    providers = db.scalars(select(AIProvider)).all()
    models = db.scalars(select(AIModel)).all()
    assert len(providers) == len(models) == 2
    assert {p.project_id for p in providers} == {p.id for p in projects}
    assert {m.provider_id for m in models} == {p.id for p in providers}
    assert all(decrypt_str(p.api_key) == PRIVATE for p in providers)
    assert model_store.await_count == 3
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
async def test_uninstall_does_not_remove_another_projects_model(
    records, model_store, monkeypatch, caplog
):
    db, projects = records
    for project in projects:
        db.add(
            StoreCredential(
                project_id=project.id,
                store_user_id="user",
                store_email="user@example.invalid",
                api_key_encrypted=encrypt_str(PRIVATE),
            )
        )
        await store._install_model_internal(
            "model-resource", project.id, PRIVATE, db
        )
    monkeypatch.setattr(
        store.store_client,
        "uninstall_model",
        AsyncMock(side_effect=RuntimeError(PRIVATE)),
    )
    model_store.side_effect = RuntimeError(PRIVATE)
    result = await store.uninstall_model_from_store(
        "model-resource", db, SimpleNamespace(project_id=projects[0].id)
    )
    assert result == {"success": True}
    assert (
        await store.list_installed_models(
            db, SimpleNamespace(project_id=projects[0].id)
        )
        == []
    )
    assert await store.list_installed_models(
        db, SimpleNamespace(project_id=projects[1].id)
    ) == ["model-name"]
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
async def test_uninstall_fetch_failure_leaves_models_untouched(
    records, model_store, monkeypatch, caplog
):
    db, projects = records
    db.add(
        StoreCredential(
            project_id=projects[0].id,
            store_user_id="user",
            store_email="user@example.invalid",
            api_key_encrypted=encrypt_str(PRIVATE),
        )
    )
    await store._install_model_internal(
        "model-resource", projects[0].id, PRIVATE, db
    )
    monkeypatch.setattr(
        store.store_client,
        "get_model",
        AsyncMock(side_effect=RuntimeError(PRIVATE)),
    )
    with pytest.raises(HTTPException) as error:
        await store.uninstall_model_from_store(
            "model-resource", db, SimpleNamespace(project_id=projects[0].id)
        )
    assert error.value.status_code == 502
    assert len(db.scalars(select(AIModel)).all()) == 1
    assert PRIVATE not in str(error.value.detail) + caplog.text


@pytest.mark.asyncio
async def test_list_failure_has_safe_log_and_existing_fallback(caplog):
    db = MagicMock()
    db.scalars.side_effect = RuntimeError(PRIVATE)
    assert (
        await store.list_installed_models(
            db, SimpleNamespace(project_id=uuid4())
        )
        == []
    )
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
async def test_uninstall_sync_only_sends_present_resource_ids(
    records, model_store, monkeypatch
):
    db, projects = records
    await store._install_model_internal(
        "model-resource", projects[0].id, PRIVATE, db
    )
    provider = db.scalar(select(AIProvider))
    db.add(
        AIModel(
            provider_id=provider.id,
            provider="openai_compatible",
            model_id="local-model",
            model_name="Local",
        )
    )
    db.commit()
    send = AsyncMock()
    monkeypatch.setattr(store_sync, "sync_uninstall_models_to_store", send)
    await store_sync.sync_uninstall_all_provider_models(
        db, projects[0].id, provider.id
    )
    send.assert_awaited_once_with(db, projects[0].id, ["model-resource"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,code", [(httpx.ConnectError, 502), (RuntimeError, 500)]
)
async def test_proxy_exception_does_not_reflect_credential_urls(
    monkeypatch, upstream, failure, code, caplog
):
    app = FastAPI()
    app.include_router(store.router, prefix="/store")
    app.dependency_overrides[
        store.get_current_active_user
    ] = lambda: SimpleNamespace()
    upstream.error = failure(
        "https://example.invalid?refresh_token=" + PRIVATE
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/store/proxy/auth/refresh?refresh_token=" + PRIVATE
        )
    assert response.status_code == code
    assert PRIVATE not in response.text
    assert PRIVATE not in caplog.text
    assert len(upstream.requests) == 1


@pytest.mark.asyncio
async def test_background_uninstall_failures_do_not_log_exception_text(
    monkeypatch, caplog
):
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(
        api_key_encrypted=encrypt_str(PRIVATE)
    )
    call = AsyncMock(side_effect=RuntimeError(PRIVATE))
    monkeypatch.setattr(store_sync.store_client, "uninstall_model", call)
    await store_sync.sync_uninstall_models_to_store(
        db, uuid4(), ["model-resource"]
    )
    assert call.await_count == 1
    assert PRIVATE not in caplog.text
