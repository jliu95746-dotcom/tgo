"""Pytest configuration and fixtures."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


@pytest.fixture
def mock_agentbay_controller():
    """Create a mock AgentBay controller."""
    controller = AsyncMock()
    controller.create_session = AsyncMock(return_value="test-session-id")
    controller.delete_session = AsyncMock(return_value=True)
    controller.take_screenshot = AsyncMock(return_value=b"fake-screenshot-data")
    controller.click = AsyncMock(return_value=True)
    controller.type_text = AsyncMock(return_value=True)
    controller.swipe = AsyncMock(return_value=True)
    controller.press_back = AsyncMock(return_value=True)
    controller.launch_app = AsyncMock(return_value=True)
    controller.restore_session = AsyncMock(return_value=True)
    controller.has_session = MagicMock(return_value=True)
    return controller


@pytest.fixture
def mock_vlm_client():
    """Create a mock VLM client."""
    client = AsyncMock()
    client.analyze_image = AsyncMock(
        return_value={
            "choices": [
                {
                    "message": {
                        "content": '{"screen_type": "conversation_list", "has_new_messages": false}'
                    }
                }
            ]
        }
    )
    return client


@pytest.fixture
def sample_platform_id():
    """Generate a sample platform ID."""
    return uuid4()


@pytest.fixture
def sample_session_id():
    """Generate a sample session ID."""
    return uuid4()
