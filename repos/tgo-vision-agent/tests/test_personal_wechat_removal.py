"""Personal WeChat cannot create cloud sessions or restore workers."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.base.app_automator import AppAutomatorFactory
from app.domain.entities import AppType
from app.services.platform_callback import PlatformCallbackService


def test_wechat_automator_removed():
    assert "wechat" not in {item.value for item in AppType}
    assert "wechat" not in AppAutomatorFactory.get_supported_apps()


@pytest.mark.asyncio
async def test_wechat_callback_rejected():
    with pytest.raises(ValueError, match="retired"):
        await PlatformCallbackService().notify_new_message(
            "test", "test", "test", "test", app_type="wechat"
        )


@pytest.mark.asyncio
async def test_wechat_session_rejected_before_cloud_or_database_access():
    from app.services.session_service import SessionService

    db = AsyncMock()
    with pytest.raises(ValueError, match="Unsupported application"):
        await SessionService(db).create_session(uuid4(), "wechat")
    assert not db.mock_calls
