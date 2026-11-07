"""Stop signals must prevent configuration commits and synchronization."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks

from app.api.v1.endpoints import ai_providers
from app.schemas.ai_provider import AIProviderUpdate


@pytest.fixture
def update_case(monkeypatch):
    model = SimpleNamespace(
        model_id="example-model",
        model_type="chat",
        deleted_at=None,
        capabilities="legacy-invalid-json",
    )
    provider = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        name="sample",
        models=[model],
        default_model=None,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = provider
    sync = AsyncMock()
    result = object()
    monkeypatch.setattr(
        ai_providers, "sync_provider_with_retry_and_update", sync
    )
    monkeypatch.setattr(ai_providers, "_to_response", lambda item: result)
    payload = AIProviderUpdate(
        available_models=[
            {
                "model_id": "example-model",
                "model_type": "chat",
                "capabilities": {"vision": True},
            }
        ]
    )

    async def update():
        return await ai_providers.update_ai_provider(
            provider.id,
            payload,
            BackgroundTasks(),
            db,
            SimpleNamespace(project_id=provider.project_id),
        )

    return SimpleNamespace(
        update=update,
        db=db,
        sync=sync,
        model=model,
        result=result,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", [asyncio.CancelledError, KeyboardInterrupt, SystemExit]
)
async def test_json_parse_stop_signal_prevents_commit_and_sync(
    update_case, kind
):
    signal = kind("stop-configuration-update")
    with patch("json.loads", side_effect=signal):
        with pytest.raises(kind) as stopped:
            await update_case.update()
    assert stopped.value is signal
    update_case.db.commit.assert_not_called()
    update_case.db.refresh.assert_not_called()
    update_case.sync.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy,expected",
    [
        ("legacy-invalid-json", {"vision": True}),
        ("null", {"vision": True}),
        ('{"vision": false}', {"vision": True}),
        ('{"vision": true}', '{"vision": true}'),
    ],
)
async def test_legacy_capability_shapes_keep_normal_update_fallback(
    update_case,
    legacy,
    expected,
):
    update_case.model.capabilities = legacy
    assert await update_case.update() is update_case.result
    assert update_case.model.capabilities == expected
    update_case.db.commit.assert_called_once()
    update_case.sync.assert_awaited_once()
