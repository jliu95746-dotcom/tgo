from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.visitor import VisitorServiceModeUpdate
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
