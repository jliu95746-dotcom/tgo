"""Message polling worker using Agent architecture.

Simplified message poller that delegates complex logic to AgentLoop.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import deque
from typing import TYPE_CHECKING, Awaitable, Callable, Optional
from uuid import UUID

from app.domain.agent.entities import AgentResult
from app.domain.base.app_automator import BaseAppAutomator, AppAutomatorFactory

if TYPE_CHECKING:
    from app.domain.agent.agent_loop import AgentLoop
    from app.domain.ports import MessageCallback

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class MessagePoller:
    """Message polling worker using Agent architecture.

    This worker:
    1. Ensures the app is running and logged in
    2. Periodically checks for new messages
    3. Notifies callbacks when new messages are detected

    All complex logic is delegated to AgentLoop.
    """

    def __init__(
        self,
        platform_id: UUID,
        app_type: str,
        agent: "AgentLoop",
        poll_interval: int = 10,
        message_callback: Optional["MessageCallback"] = None,
        login_status_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        """Initialize the message poller.

        Args:
            platform_id: Platform ID from tgo-api
            app_type: Application type (wechat, douyin, etc.)
            agent: AgentLoop instance for task execution
            poll_interval: Polling interval in seconds
            message_callback: Callback for new messages
            login_status_callback: Callback for persisting app login status
        """
        self.platform_id = platform_id
        self.app_type = app_type
        self.agent = agent
        self.poll_interval = poll_interval
        self.message_callback = message_callback
        self.login_status_callback = login_status_callback

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._automator: Optional[BaseAppAutomator] = None
        self._last_status: Optional[str] = None
        self._processed_fingerprints: set[str] = set()
        self._fingerprint_order: deque[str] = deque()
        self._max_processed_fingerprints = 10000
        self._consecutive_errors = 0
        self._max_consecutive_errors = 5

    def _get_automator(self) -> BaseAppAutomator:
        """Get or create the app automator."""
        if self._automator is None:
            self._automator = AppAutomatorFactory.create(
                self.app_type,
                self.agent,
                self.agent.session_id,
            )
        return self._automator

    async def start(self) -> None:
        """Start the message polling loop."""
        if self._running:
            logger.warning("Message poller already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            f"Started message poller for platform {self.platform_id}, "
            f"app {self.app_type}, interval {self.poll_interval}s"
        )

    async def stop(self) -> None:
        """Stop the message polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"Stopped message poller for platform {self.platform_id}")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._poll_once()
                self._consecutive_errors = 0
            except Exception as e:
                self._consecutive_errors += 1
                logger.error(f"Error in poll loop: {e}", exc_info=True)

                if self._consecutive_errors >= self._max_consecutive_errors:
                    logger.error(
                        f"Too many consecutive errors ({self._consecutive_errors}), "
                        f"stopping poller for platform {self.platform_id}"
                    )
                    await self._notify_status("error", "轮询出错次数过多，已停止")
                    break

            await asyncio.sleep(self.poll_interval)

    async def _poll_once(self) -> None:
        """Single polling iteration using Agent."""
        automator = self._get_automator()

        # Step 1: Ensure app is running and ready
        # Use AgentLoop to handle all states (not installed, not running, not logged in)
        result = await automator.run_custom_task(
            goal=f"确保 {automator.get_app_display_name()} 正在运行并已登录，"
            f"如果未安装则安装，如果未登录则等待扫码，最后导航到消息列表",
            max_steps=30,
        )

        if not result.success:
            logger.warning(f"Failed to ensure app ready: {result.message}")
            await self._notify_status("not_ready", result.message)
            return

        login_status = str(result.data.get("login_status", "offline"))
        if login_status != "logged_in":
            await self._notify_status(
                login_status,
                f"{automator.get_app_display_name()} 尚未登录",
            )
            return

        await self._notify_status(
            "logged_in",
            f"{automator.get_app_display_name()} 已登录",
        )

        # Step 2: Check for new messages
        check_result = await automator.run_custom_task(
            goal="检查消息列表，识别有未读消息红点的联系人，提取联系人名称列表",
            max_steps=5,
        )

        if check_result.success:
            # Process any detected new messages
            await self._process_messages(check_result)

    async def _process_messages(self, result: AgentResult) -> None:
        """Process messages from agent result.

        The agent should return message info in result.data.
        """
        contacts = result.data.get("contacts", [])
        if not isinstance(contacts, list):
            logger.warning("Agent returned invalid contacts payload")
            return

        logger.info(f"Detected {len(contacts)} contacts with new messages")
        for contact in contacts:
            if not isinstance(contact, dict):
                continue

            contact_name = str(contact.get("name", "")).strip()
            contact_id = str(contact.get("id") or contact_name).strip()
            message_content = str(contact.get("preview", "")).strip()
            if not contact_id or not message_content:
                logger.warning("Skipping incomplete contact payload: %s", contact)
                continue

            fingerprint = self._generate_fingerprint(contact_id, message_content)
            if self._is_message_processed(fingerprint):
                logger.debug("Skipping duplicate message for contact %s", contact_id)
                continue

            if self.message_callback:
                try:
                    delivered = await self.message_callback.notify_new_message(
                        platform_id=str(self.platform_id),
                        contact_id=contact_id,
                        contact_name=contact_name,
                        message_content=message_content,
                        message_type="text",
                    )
                    if delivered:
                        self._mark_message_processed(fingerprint)
                except Exception as e:
                    logger.error(f"Failed to notify message: {e}")

    @staticmethod
    def _generate_fingerprint(contact_id: str, message_content: str) -> str:
        """Generate a stable fingerprint for a visible message preview."""
        value = f"{contact_id}\0{message_content}".encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    def _is_message_processed(self, fingerprint: str) -> bool:
        """Check whether a message preview was already delivered."""
        return fingerprint in self._processed_fingerprints

    def _mark_message_processed(self, fingerprint: str) -> None:
        """Remember a delivered message while keeping memory bounded."""
        if fingerprint in self._processed_fingerprints:
            return

        if len(self._fingerprint_order) >= self._max_processed_fingerprints:
            oldest = self._fingerprint_order.popleft()
            self._processed_fingerprints.discard(oldest)

        self._fingerprint_order.append(fingerprint)
        self._processed_fingerprints.add(fingerprint)

    async def _notify_status(self, status: str, message: str) -> None:
        """Notify about status change."""
        if status == self._last_status:
            return

        self._last_status = status
        logger.info(f"Status change for {self.platform_id}: {status} - {message}")

        if self.login_status_callback and status in {
            "logged_in",
            "qr_pending",
            "offline",
            "expired",
            "app_not_installed",
            "app_installing",
            "app_not_running",
        }:
            try:
                await self.login_status_callback(status)
            except Exception as e:
                logger.error(f"Failed to persist login status: {e}")

        if self.message_callback:
            try:
                await self.message_callback.notify_status_change(
                    platform_id=str(self.platform_id),
                    status=status,
                    message=message,
                )
            except Exception as e:
                logger.error(f"Failed to notify status change: {e}")
