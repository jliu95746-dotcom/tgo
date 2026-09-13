"""Live database shape for tenant-owned multimodal model selection."""

import hashlib
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.llm_model import LLMModel
from app.models.llm_provider import LLMProvider
from app.models.project_ai_config import ProjectAIConfig
from app.schemas.multimodal import (
    AnalysisErrorCategory,
    MediaAnalysisRequest,
    MediaType,
)
from app.services.configured_multimodal import ConfiguredMultimodalService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["ready", "foreign", "disabled", "text-only", "missing"]
)
async def test_only_explicit_owned_enabled_models_are_called(mode):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [LLMProvider.__table__, LLMModel.__table__, ProjectAIConfig.__table__]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: LLMProvider.metadata.create_all(conn, tables=tables)
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, provider_id = uuid.uuid4(), uuid.uuid4()
    content = b"\x89PNG\r\n\x1a\nowned-image"
    calls = []

    async def transport(request):
        calls.append(request)
        # OCR and VLM deliberately share one explicitly configured model.
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"text":"订单 A1001"}'},
                    }
                ]
            },
        )

    async with sessions() as db:
        provider = LLMProvider(
            id=provider_id,
            project_id=uuid.uuid4() if mode == "foreign" else project_id,
            alias="fixture-provider",
            provider_kind="openai_compatible",
            api_base_url="https://provider.example/v1",
            api_key="fixture-only-key",
            is_active=mode != "disabled",
        )
        db.add(provider)
        db.add(
            LLMModel(
                id=uuid.uuid4(),
                provider_id=provider_id,
                model_id="fixture-vision",
                model_name="fixture-vision",
                model_type="chat",
                capabilities={"vision": mode != "text-only"},
                is_active=True,
            )
        )
        if mode != "missing":
            db.add(
                ProjectAIConfig(
                    project_id=project_id,
                    default_ocr_provider_id=provider_id,
                    default_ocr_model="fixture-vision",
                    default_vlm_provider_id=provider_id,
                    default_vlm_model="fixture-vision",
                )
            )
        await db.commit()
        request = MediaAnalysisRequest(
            media_id="fixture",
            media_uri="tgo-media://fixture/image",
            media_type=MediaType.IMAGE,
            mime_type="image/png",
            sha256=hashlib.sha256(content).hexdigest(),
        )
        service = ConfiguredMultimodalService(
            db, transport=httpx.MockTransport(transport)
        )
        result = await service.analyze(project_id, request, content)
        if mode == "ready":
            assert result.can_continue and len(calls) == 2
        else:
            assert not result.can_continue and not calls
            assert all(
                s.error.category is AnalysisErrorCategory.PROVIDER_NOT_CONFIGURED
                for s in result.stages
            )
        assert (
            result.fallback_message is None or "已转人工" not in result.fallback_message
        )
    await engine.dispose()
