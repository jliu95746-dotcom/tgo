"""Knowledge-governance contracts shared with the AI runtime."""

from enum import Enum


class KnowledgeChannel(str, Enum):
    """Channels supported by automatic-answer knowledge governance."""

    WECOM_KF = "wecom_kf"
    WEB = "web"
    APP = "app"
    PHONE = "phone"
    INTERNAL = "internal"
