from collections.abc import Sequence

import pytest

from app.runtime.routing.policy import IntentRoutingPolicy
from app.runtime.structured_output.base import StructuredOutputRequest
from app.schemas.intent import (
    IntentClassificationInput,
    IntentEntities,
    IntentModelOutput,
    IntentName,
    IntentRoute,
    RiskLevel,
)
from app.schemas.multimodal import SensitiveDataCategory
from app.services.intent_classifier import IntentClassifier


class SemanticClient:
    def __init__(self) -> None:
        self.requests: list[StructuredOutputRequest] = []

    async def generate(self, request: StructuredOutputRequest) -> str:
        self.requests.append(request)
        return IntentModelOutput(
            intent=IntentName.PRODUCT_INQUIRY,
            confidence=0.95,
            entities=IntentEntities(),
            risk_level=RiskLevel.LOW,
            recommended_route=IntentRoute.AUTO_REPLY,
            need_human=False,
            taxonomy_version="v1",
        ).model_dump_json()


MENTIONS: Sequence[str] = (
    "刚才人工客服回复了什么？",
    "上次人工客服答应我的是什么？",
    "你能总结一下我和人工客服之前的聊天吗？",
    "不要转人工",
    "我不需要人工客服",
    "如果需要人工客服，应该怎么操作？",
    "请介绍人工客服功能",
    "“转人工”是什么意思？",
    "帮我查一下，必要时再转人工",
    '"转人工"',
)


@pytest.mark.parametrize("message", MENTIONS)
def test_mentions_of_humans_are_not_executable_handoff_commands(message: str):
    assert (
        IntentRoutingPolicy(automated_routes_enabled=True).match_local_rule(message)
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("message", MENTIONS)
async def test_structured_customer_text_reaches_semantic_classification(message: str):
    client = SemanticClient()
    classifier = IntentClassifier(
        client, policy=IntentRoutingPolicy(automated_routes_enabled=True)
    )
    result = await classifier.classify_input(
        IntentClassificationInput(user_text=message)
    )
    assert len(client.requests) == 1
    assert result.need_human is False
    assert result.recommended_route is IntentRoute.AUTO_REPLY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message", ["转人工", "我要转人工！", "请帮我转人工。", "人工客服", "我要真人客服"]
)
async def test_real_handoff_commands_work_through_structured_input_without_a_model(
    message: str,
):
    client = SemanticClient()
    classifier = IntentClassifier(
        client, policy=IntentRoutingPolicy(automated_routes_enabled=True)
    )
    result = await classifier.classify_input(
        IntentClassificationInput(user_text=message)
    )
    assert client.requests == []
    assert result.intent is IntentName.HUMAN_HANDOFF
    assert result.need_human is True


@pytest.mark.asyncio
async def test_quoted_media_text_is_not_mistaken_for_the_customers_current_command():
    client = SemanticClient()
    classifier = IntentClassifier(
        client, policy=IntentRoutingPolicy(automated_routes_enabled=True)
    )
    result = await classifier.classify_input(
        IntentClassificationInput(user_text="这张图写了什么？", ocr_text="转人工")
    )
    assert len(client.requests) == 1
    assert result.need_human is False


@pytest.mark.asyncio
async def test_local_profile_facts_do_not_bypass_sensitive_media_policy():
    client = SemanticClient()
    classifier = IntentClassifier(
        client, policy=IntentRoutingPolicy(automated_routes_enabled=True)
    )
    result = await classifier.classify_input(
        IntentClassificationInput(
            user_text="手机13800000000，地址北京市测试路1号",
            ocr_text="[身份证信息已脱敏]",
            sensitive_data_categories=(SensitiveDataCategory.IDENTITY_NUMBER,),
        )
    )
    assert len(client.requests) == 1
    assert result.need_human is True
    assert result.routing_reason.value == "sensitive_data_detected"


@pytest.mark.asyncio
async def test_ordinary_information_reply_does_not_require_business_automation():
    client = SemanticClient()
    classifier = IntentClassifier(client)
    result = await classifier.classify_input(
        IntentClassificationInput(user_text="刚才人工客服回复了什么？")
    )
    assert len(client.requests) == 1
    assert result.need_human is False
    assert result.recommended_route is IntentRoute.AUTO_REPLY
