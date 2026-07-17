from __future__ import annotations

import logging

from app import main


def test_http_client_info_logs_are_disabled_to_protect_query_secrets() -> None:
    assert main.app is not None
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
