from __future__ import annotations

from .base import (
    BasePlatformAdapter as BasePlatformAdapter,
    SimpleStdoutAdapter as SimpleStdoutAdapter,
)
from .email import EmailAdapter as EmailAdapter
from .wecom import WeComAdapter as WeComAdapter
from .wecom_bot import WeComBotAdapter as WeComBotAdapter
from .feishu_bot import FeishuBotAdapter as FeishuBotAdapter
from .dingtalk_bot import DingTalkBotAdapter as DingTalkBotAdapter
from .telegram import TelegramAdapter as TelegramAdapter
from .slack import SlackAdapter as SlackAdapter
from .vision_agent import VisionAgentAdapter as VisionAgentAdapter
