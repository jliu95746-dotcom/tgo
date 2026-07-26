"""Tests for the Agent-based message polling worker."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.agent.entities import AgentResult
from app.workers.message_poller import MessagePoller


def _build_poller(sample_platform_id):
    agent = MagicMock()
    agent.session_id = "test-session"
    callback = AsyncMock()
    callback.notify_new_message.return_value = True
    callback.notify_status_change.return_value = True
    login_status_callback = AsyncMock()

    poller = MessagePoller(
        platform_id=sample_platform_id,
        app_type="wechat",
        agent=agent,
        poll_interval=5,
        message_callback=callback,
        login_status_callback=login_status_callback,
    )
    return poller, callback, login_status_callback


class TestMessagePoller:
    """Tests for MessagePoller class."""

    def test_init(self, sample_platform_id):
        poller, _, _ = _build_poller(sample_platform_id)

        assert poller.platform_id == sample_platform_id
        assert poller.app_type == "wechat"
        assert poller.agent.session_id == "test-session"
        assert poller.poll_interval == 5
        assert poller._running is False

    def test_generate_fingerprint(self, sample_platform_id):
        poller, _, _ = _build_poller(sample_platform_id)

        fp1 = poller._generate_fingerprint("contact1", "hello")
        fp2 = poller._generate_fingerprint("contact1", "hello")
        fp3 = poller._generate_fingerprint("contact2", "hello")

        assert fp1 == fp2
        assert fp1 != fp3

    def test_mark_message_processed_bounds_set_size(self, sample_platform_id):
        poller, _, _ = _build_poller(sample_platform_id)

        for index in range(10005):
            poller._mark_message_processed(f"fp-{index}")

        assert len(poller._processed_fingerprints) <= 10000

    @pytest.mark.asyncio
    async def test_poll_once_persists_login_and_forwards_unread_contact(
        self,
        sample_platform_id,
    ):
        poller, callback, login_status_callback = _build_poller(sample_platform_id)
        automator = MagicMock()
        automator.get_app_display_name.return_value = "微信"
        automator.run_custom_task = AsyncMock(
            side_effect=[
                AgentResult(
                    success=True,
                    message="ready",
                    data={"login_status": "logged_in"},
                ),
                AgentResult(
                    success=True,
                    message="found",
                    data={
                        "contacts": [
                            {
                                "id": "wx-user-1",
                                "name": "测试客户",
                                "preview": "请问今天营业吗？",
                            }
                        ]
                    },
                ),
            ]
        )

        with patch(
            "app.workers.message_poller.AppAutomatorFactory.create",
            return_value=automator,
        ):
            await poller._poll_once()

        login_status_callback.assert_awaited_once_with("logged_in")
        callback.notify_new_message.assert_awaited_once_with(
            platform_id=str(sample_platform_id),
            contact_id="wx-user-1",
            contact_name="测试客户",
            message_content="请问今天营业吗？",
            message_type="text",
        )

    @pytest.mark.asyncio
    async def test_poll_once_stops_before_message_scan_when_not_logged_in(
        self,
        sample_platform_id,
    ):
        poller, callback, login_status_callback = _build_poller(sample_platform_id)
        automator = MagicMock()
        automator.get_app_display_name.return_value = "微信"
        automator.run_custom_task = AsyncMock(
            return_value=AgentResult(
                success=True,
                message="waiting for QR scan",
                data={"login_status": "qr_pending"},
            )
        )

        with patch(
            "app.workers.message_poller.AppAutomatorFactory.create",
            return_value=automator,
        ):
            await poller._poll_once()

        assert automator.run_custom_task.await_count == 1
        login_status_callback.assert_awaited_once_with("qr_pending")
        callback.notify_new_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_message_is_forwarded_only_once(self, sample_platform_id):
        poller, callback, _ = _build_poller(sample_platform_id)
        result = AgentResult(
            success=True,
            message="found",
            data={
                "contacts": [
                    {
                        "id": "wx-user-1",
                        "name": "测试客户",
                        "preview": "同一条消息",
                    }
                ]
            },
        )

        await poller._process_messages(result)
        await poller._process_messages(result)

        assert callback.notify_new_message.await_count == 1

    @pytest.mark.asyncio
    async def test_failed_callback_is_not_marked_processed(self, sample_platform_id):
        poller, callback, _ = _build_poller(sample_platform_id)
        callback.notify_new_message.return_value = False
        result = AgentResult(
            success=True,
            message="found",
            data={
                "contacts": [
                    {
                        "id": "wx-user-1",
                        "name": "测试客户",
                        "preview": "需要重试",
                    }
                ]
            },
        )

        await poller._process_messages(result)
        await poller._process_messages(result)

        assert callback.notify_new_message.await_count == 2

    @pytest.mark.asyncio
    async def test_start_stop(self, sample_platform_id):
        poller, _, _ = _build_poller(sample_platform_id)

        with patch.object(poller, "_poll_loop", new=AsyncMock()):
            await poller.start()
            assert poller._running is True

            await poller.stop()
            assert poller._running is False
