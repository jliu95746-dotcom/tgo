"""Fail-closed, rule-first routing policy for classified customer messages."""

from __future__ import annotations

import re
import unicodedata

from app.schemas.intent import (
    ClassificationSource,
    IntentClassificationResult,
    IntentEntities,
    IntentModelOutput,
    IntentName,
    IntentRoute,
    IntentRoutingContext,
    RiskLevel,
    RoutingReason,
)


class IntentRoutingPolicy:
    """Enforce safety and confidence rules independently of model
    suggestions.
    """

    LOW_CONFIDENCE_THRESHOLD = 0.60
    HIGH_CONFIDENCE_THRESHOLD = 0.85

    _READ_ONLY_TOOL_INTENTS = frozenset(
        {IntentName.ORDER_QUERY, IntentName.LOGISTICS_QUERY}
    )
    _SENSITIVE_INTENTS = frozenset({IntentName.COMPLAINT, IntentName.HUMAN_HANDOFF})
    _HANDOFF_PHRASES = (
        "转人工",
        "人工客服",
        "真人客服",
        "人工服务",
        "找人工",
        "人工处理",
        "客服介入",
    )
    _COMPLAINT_PHRASES = (
        "投诉",
        "举报",
        "维权",
    )
    _WRITE_ACTION_PHRASES = (
        "立即退款",
        "直接退款",
        "帮我退款",
        "申请退款",
        "立即退货",
        "申请退货",
        "取消订单",
        "修改订单",
        "修改收货地址",
        "修改地址",
        "帮我改价",
        "修改账户",
        "变更账户",
        "我要退款",
        "退钱",
        "换货",
        "注销账号",
        "注销账户",
        "修改手机号",
        "修改密码",
    )

    _PROFILE_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
    _PROFILE_PHONE_LABELS = (
        "手机号",
        "手机",
        "联系电话",
        "电话",
        "联系方式",
    )
    _PROFILE_ADDRESS_LABELS = (
        "收货地址",
        "配送地址",
        "联系地址",
        "住址",
        "地址",
    )
    _PROFILE_ADDRESS_HINTS = (
        "省",
        "市",
        "区",
        "县",
        "镇",
        "街",
        "路",
        "号",
        "栋",
        "单元",
        "室",
    )
    _PROFILE_FAST_PATH_BLOCKERS = (
        "密码",
        "验证码",
        "银行卡",
        "身份证",
        "支付账户",
        "支付账号",
        "快递单号",
        "物流单号",
        "运单号",
        "订单号",
        "token",
        "secret",
    )

    def __init__(self, *, automated_routes_enabled: bool = False) -> None:
        self._automated_routes_enabled = automated_routes_enabled

    def match_local_rule(self, text: str) -> IntentClassificationResult | None:
        """Match explicit safety rules before any remote model invocation."""
        normalized_text = "".join(
            character
            for character in unicodedata.normalize("NFKC", text).lower()
            if not unicodedata.category(character).startswith(("P", "Z", "S"))
        )
        if self._contains(normalized_text, self._HANDOFF_PHRASES):
            return self._rule_result(IntentName.HUMAN_HANDOFF)
        if self._contains(normalized_text, self._COMPLAINT_PHRASES):
            return self._rule_result(IntentName.COMPLAINT)
        if self._contains(normalized_text, self._WRITE_ACTION_PHRASES):
            write_intent = self._write_action_intent(normalized_text)
            return self._rule_result(write_intent)
        if self._looks_like_explicit_profile_facts(normalized_text):
            if not self._automated_routes_enabled:
                return self._rule_result(IntentName.SALES_LEAD)
            return self._profile_fact_result()
        return None

    def decide(
        self,
        output: IntentModelOutput,
        context: IntentRoutingContext | None = None,
    ) -> IntentClassificationResult:
        """Replace model-suggested routing with deterministic local policy."""
        routing_context = context or IntentRoutingContext()
        if routing_context.media_processing_failed:
            return self._result(
                output,
                IntentRoute.HUMAN_HANDOFF,
                True,
                RoutingReason.MEDIA_PROCESSING_FAILED,
            )
        if routing_context.contains_sensitive_data:
            return self._result(
                output,
                IntentRoute.HUMAN_HANDOFF,
                True,
                RoutingReason.SENSITIVE_DATA_DETECTED,
            )
        if output.risk_level is RiskLevel.HIGH:
            return self._result(
                output,
                IntentRoute.HUMAN_HANDOFF,
                True,
                RoutingReason.HIGH_RISK,
            )
        if output.risk_level is RiskLevel.MEDIUM:
            return self._result(
                output,
                IntentRoute.CLARIFY,
                False,
                RoutingReason.MEDIUM_RISK,
            )
        if output.intent in self._SENSITIVE_INTENTS:
            reason = (
                RoutingReason.REPEATED_UNKNOWN
                if output.intent is IntentName.UNKNOWN
                and routing_context.consecutive_unknown_count >= 2
                else RoutingReason.SENSITIVE_INTENT
            )
            return self._result(
                output,
                IntentRoute.HUMAN_HANDOFF,
                True,
                reason,
            )
        if output.intent is IntentName.UNKNOWN:
            if routing_context.consecutive_unknown_count >= 2:
                return self._result(
                    output,
                    IntentRoute.HUMAN_HANDOFF,
                    True,
                    RoutingReason.REPEATED_UNKNOWN,
                )
            return self._result(
                output,
                IntentRoute.CLARIFY,
                False,
                RoutingReason.UNKNOWN_CLARIFICATION,
            )
        if output.confidence < self.LOW_CONFIDENCE_THRESHOLD:
            return self._result(
                output,
                IntentRoute.HUMAN_HANDOFF,
                True,
                RoutingReason.LOW_CONFIDENCE,
            )
        if output.confidence < self.HIGH_CONFIDENCE_THRESHOLD:
            return self._result(
                output,
                IntentRoute.CLARIFY,
                False,
                RoutingReason.MEDIUM_CONFIDENCE,
            )
        if (
            output.intent in self._READ_ONLY_TOOL_INTENTS
            and routing_context.contains_untrusted_media_text
        ):
            return self._result(
                output,
                IntentRoute.CLARIFY,
                False,
                RoutingReason.UNTRUSTED_MEDIA_CONFIRMATION,
            )
        if not self._automated_routes_enabled:
            return self._result(
                output,
                IntentRoute.HUMAN_HANDOFF,
                True,
                RoutingReason.AUTOMATION_DISABLED,
            )
        if output.intent in self._READ_ONLY_TOOL_INTENTS:
            return self._result(
                output,
                IntentRoute.READ_ONLY_TOOL,
                False,
                RoutingReason.HIGH_CONFIDENCE_READ_ONLY,
            )
        return self._result(
            output,
            IntentRoute.AUTO_REPLY,
            False,
            RoutingReason.HIGH_CONFIDENCE_FAQ,
        )

    def fail_closed(self) -> IntentClassificationResult:
        """Return the only safe result when classification cannot be
        trusted.
        """
        return IntentClassificationResult(
            intent=IntentName.UNKNOWN,
            confidence=0.0,
            entities=IntentEntities(),
            risk_level=RiskLevel.HIGH,
            recommended_route=IntentRoute.HUMAN_HANDOFF,
            need_human=True,
            taxonomy_version="v1",
            routing_reason=RoutingReason.CLASSIFICATION_FAILED,
            classification_source=ClassificationSource.FAIL_CLOSED,
        )

    @staticmethod
    def _contains(text: str, phrases: tuple[str, ...]) -> bool:
        return any(phrase in text for phrase in phrases)

    @classmethod
    def _looks_like_explicit_profile_facts(cls, text: str) -> bool:
        """Recognize contact facts without treating secrets as profile data."""

        if cls._contains(text, cls._PROFILE_FAST_PATH_BLOCKERS):
            return False

        has_phone = cls._PROFILE_PHONE_PATTERN.search(text) is not None
        has_phone_label = has_phone and cls._contains(
            text,
            cls._PROFILE_PHONE_LABELS,
        )
        has_address_label = cls._contains(text, cls._PROFILE_ADDRESS_LABELS)
        address_hint_count = sum(
            hint in text for hint in cls._PROFILE_ADDRESS_HINTS
        )
        has_unlabelled_address = len(text) >= 8 and address_hint_count >= 3

        return (
            has_phone_label
            or has_address_label
            or (has_phone and has_unlabelled_address)
            or has_unlabelled_address
        )

    @staticmethod
    def _write_action_intent(text: str) -> IntentName:
        if "退款" in text or "退货" in text:
            return IntentName.REFUND_RETURN_INQUIRY
        if "改价" in text:
            return IntentName.PRICING_PROMOTION
        if "订单" in text or "地址" in text:
            return IntentName.ORDER_ASSISTANCE
        return IntentName.UNKNOWN

    @staticmethod
    def _rule_result(intent: IntentName) -> IntentClassificationResult:
        return IntentClassificationResult(
            intent=intent,
            confidence=1.0,
            entities=IntentEntities(),
            risk_level=RiskLevel.HIGH,
            recommended_route=IntentRoute.HUMAN_HANDOFF,
            need_human=True,
            taxonomy_version="v1",
            routing_reason=RoutingReason.RULE_MATCH,
            classification_source=ClassificationSource.RULE,
        )

    @staticmethod
    def _profile_fact_result() -> IntentClassificationResult:
        return IntentClassificationResult(
            intent=IntentName.SALES_LEAD,
            confidence=1.0,
            entities=IntentEntities(),
            risk_level=RiskLevel.LOW,
            recommended_route=IntentRoute.AUTO_REPLY,
            need_human=False,
            taxonomy_version="v1",
            routing_reason=RoutingReason.RULE_MATCH,
            classification_source=ClassificationSource.RULE,
        )

    @staticmethod
    def _result(
        output: IntentModelOutput,
        route: IntentRoute,
        need_human: bool,
        reason: RoutingReason,
    ) -> IntentClassificationResult:
        return IntentClassificationResult(
            intent=output.intent,
            confidence=output.confidence,
            entities=output.entities,
            risk_level=output.risk_level,
            recommended_route=route,
            need_human=need_human,
            taxonomy_version=output.taxonomy_version,
            routing_reason=reason,
            classification_source=ClassificationSource.MODEL,
        )
