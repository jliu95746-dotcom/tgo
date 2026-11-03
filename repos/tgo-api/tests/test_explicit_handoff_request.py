"""A customer handoff command must never rely on model classification."""

import pytest

from app.services import human_handoff_service


@pytest.mark.parametrize("message", [
    "转人工", " 转人工！", "我要人工客服", "请帮我转人工。",
])
def test_explicit_handoff_command(message: str) -> None:
    assert human_handoff_service.is_explicit_handoff_request(message)


@pytest.mark.parametrize("message", [
    "不要转人工", "你说转人工是什么意思？", "请介绍人工客服功能",
    "帮我查订单，必要时转人工", "",
])
def test_quoted_or_conditional_handoff_is_not_a_command(message: str) -> None:
    assert not human_handoff_service.is_explicit_handoff_request(message)
