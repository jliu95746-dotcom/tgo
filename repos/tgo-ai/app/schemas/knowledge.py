"""Shared knowledge-governance request contracts."""

from enum import Enum


class KnowledgeChannel(str, Enum):
    """Customer channels supported by governed automatic-answer retrieval."""

    WECOM_KF = "wecom_kf"
    WEB = "web"
    APP = "app"
    PHONE = "phone"
    INTERNAL = "internal"
