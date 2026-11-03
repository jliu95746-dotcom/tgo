from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.visitor import VisitorServiceModeUpdate, populate_visitor_ai_settings
from app.services.chat_service import resolve_service_mode
from app.services import visitor_service


def test_explicit_assist_mode_never_auto_sends() -> None:
    platform = SimpleNamespace(ai_mode="auto")
    visitor = SimpleNamespace(service_mode="assist", ai_disabled=True)

    assert resolve_service_mode(platform, visitor) == "assist"


def test_legacy_ai_disabled_still_overrides_explicit_auto_mode() -> None:
    platform = SimpleNamespace(ai_mode="auto")
    visitor = SimpleNamespace(service_mode="auto", ai_disabled=True)

    assert resolve_service_mode(platform, visitor) == "manual"


def test_legacy_visitors_fall_back_to_existing_ai_fields() -> None:
    platform = SimpleNamespace(ai_mode="assist")
    visitor = SimpleNamespace(service_mode=None, ai_disabled=None)

    assert resolve_service_mode(platform, visitor) == "assist"


@pytest.mark.parametrize("legacy_mode", [None, "auto", "assist", "off"])
def test_personal_wechat_uses_the_standard_ai_default(legacy_mode) -> None:
    platform = SimpleNamespace(
        type="wechat_local", ai_mode=legacy_mode, fallback_to_ai_timeout=60,
    )
    visitor = SimpleNamespace(service_mode=None, ai_disabled=None)
    response = populate_visitor_ai_settings(SimpleNamespace(), platform)

    assert resolve_service_mode(platform, visitor) == "auto"
    assert response.ai_settings.ai_mode == "auto"
    assert response.ai_settings.fallback_to_ai_timeout is None
    visitor.ai_disabled = True
    assert resolve_service_mode(platform, visitor) == "manual"
    visitor.service_mode = "assist"
    assert resolve_service_mode(platform, visitor) == "assist"


@pytest.mark.asyncio
async def test_manual_mode_cannot_leave_humanization_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visitor = SimpleNamespace()
    load_skill = AsyncMock(return_value="published prompt")
    monkeypatch.setattr(
        visitor_service,
        "get_humanization_skill_prompt",
        load_skill,
    )

    await visitor_service.configure_service_mode(
        visitor,
        "project-1",
        VisitorServiceModeUpdate(
            service_mode="manual",
            humanization_skill_name="friendly-after-sales",
            humanization_skill_enabled=True,
        ),
    )

    assert visitor.service_mode == "manual"
    assert visitor.ai_disabled is True
    assert visitor.humanization_skill_enabled is False
    load_skill.assert_awaited_once_with("project-1", "friendly-after-sales")
