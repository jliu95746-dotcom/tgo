"""Scenario-aware retrieval; examples teach expression, never catalog facts."""

import re

from app.schemas.humanization import ConversationTurn, TrainingExample


INTENTS = {
    "color": r"颜色|[红绿蓝黑白黄紫粉棕灰橙]色|什么色",
    "material": r"材质|面料|羊皮|牛皮|真皮|人造革|纯棉",
    "price": r"价格|价钱|多少钱|售价|怎么卖|几块|费用|收费",
    "stock": r"有货|库存|缺货|现货|补货|还有吗",
    "shipping": r"物流|发货|快递|送到|还没到|配送|运费",
    "returns": r"退款|退货|售后|换货|退换|保修",
    "recommendation": r"推荐|怎么选|哪个好|适合|帮.{0,3}选",
}


def intents(text: str) -> set[str]:
    return {name for name, pattern in INTENTS.items() if re.search(pattern, text)}


def result_state(facts: str) -> str:
    if re.search(r"查询失败|查询超时|系统异常|暂未查询成功", facts):
        return "failed"
    if re.search(r"(?:未|没)(?:有)?(?:找到|查到)|无匹配|没有匹配", facts):
        return "not_found"
    if re.search(
        r"不确定|无法确认|不能确认|尚未确认|还不能确定|未明确|待核实|没.{0,2}确认"
        r"|确认不了|确定不了|无法确定|不能确定|不清楚|说不准",
        facts,
    ):
        return "unknown"
    # '有没有' asks about availability; its suffix is not a negative answer.
    if re.search(r"(?<!有)没有|没货|无货|不提供|不支持|不存在|售罄", facts):
        return "absent"
    if re.search(r"有货|有现货|库存充足|已确认有", facts):
        return "available"
    return "other"


def text_tokens(text: str) -> set[str]:
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", text.lower())
    return {text[i:i + 2] for i in range(max(0, len(text) - 1))}


def match_examples(
    examples: list[TrainingExample], query: str, factual_draft: str = "",
    recent_messages: list[ConversationTurn] | None = None,
) -> list[TrainingExample]:
    # Use prior conditions for elliptical follow-ups such as '那这个呢？'.
    history = " ".join(t.content for t in (recent_messages or [])[-4:])
    query_intents = intents(query) or intents(history)
    query_tokens = text_tokens(query + " " + history)
    state = result_state(factual_draft)
    ranked: list[tuple[float, TrainingExample]] = []
    for sample in examples:
        if sample.change_kind != "expression":
            continue
        sample_state = result_state(sample.ai_draft)
        if factual_draft and sample_state != state:
            continue
        sample_intents = intents(sample.customer_message) or intents(
            " ".join(sample.scene_tags)
        )
        shared_intents = query_intents & sample_intents
        if query_intents and not shared_intents:
            continue
        candidate_tokens = text_tokens(
            sample.customer_message + " ".join(sample.scene_tags)
        )
        lexical = len(query_tokens & candidate_tokens) / max(
            1, len(query_tokens | candidate_tokens)
        )
        if shared_intents or lexical >= 0.08:
            ranked.append((len(shared_intents) + lexical, sample))
    ranked.sort(key=lambda item: (-item[0], item[1].id))
    return [sample for _, sample in ranked[:3]]
