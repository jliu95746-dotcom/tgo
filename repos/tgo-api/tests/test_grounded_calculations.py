"""Derived numbers require source-bound arithmetic and the full fact audit."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.knowledge_evidence import KnowledgeDocument, KnowledgeEvidence
from app.services.current_knowledge import UNCONFIRMED_REPLY
from app.services.humanization_service import rewrite_assist_draft
from app.services.reply_quality import assess_reply, numeric_values
from app.services.grounded_calculations import (
    CalculationHint, calculation_feedback, calculation_sources,
)


POLICY = (
    "980元兑换3000积分；2980元兑换10000积分；7980元兑换100000积分。"
    "生成一篇文章扣2积分，发布一篇文章扣1积分。"
    "补充充值单次最低1000元，按所选方案的金额与积分比例计算。"
)
QUESTION = "那如果我每天生成10篇文章的话，我大概1000块钱能用多久？"


def evidence(content: str = POLICY) -> KnowledgeEvidence:
    return KnowledgeEvidence(
        status="matched", retrieved_at=datetime.now(timezone.utc),
        project_id="project", channel="wecom_kf",
        documents=[KnowledgeDocument(
            document_id="policy", collection_id="collection", content=content,
        )],
    )


def operand(name: str, value: str, quote: str,
            source_id: str = "document:policy") -> dict[str, str]:
    return {"name": name, "value": value, "source_id": source_id, "quote": quote}


def proof(expression: str, result: str, claim: str,
          inputs: list[dict[str, str]], rounding: str = "exact",
          decimal_places: int = 0) -> dict[str, object]:
    return {"expression": expression, "result": result, "claim": claim,
            "inputs": inputs, "rounding": rounding, "decimal_places": decimal_places}


def geo_inputs() -> list[dict[str, str]]:
    return [
        operand("price", "980", "980元兑换3000积分"),
        operand("credits", "3000", "980元兑换3000积分"),
        operand("budget", "1000", "1000块钱", "customer:current"),
        operand("daily", "10", "每天生成10篇文章", "customer:current"),
        operand("generate", "2", "生成一篇文章扣2积分"),
    ]


async def rewrite(reply: str, calculations: list[dict[str, object]], *,
                  content: str = POLICY, question: str = QUESTION,
                  valid: bool = True, issues: list[str] | None = None,
                  recent_messages: list[dict[str, str]] | None = None,
                  factual_draft: str | None = None) -> tuple[str, AsyncMock]:
    async def model(**kwargs: object) -> dict[str, str]:
        if kwargs.get("trace_phase") == "fact_audit":
            return {"content": json.dumps({
                "valid": valid, "issues": issues or [], "calculations": calculations,
            }, ensure_ascii=False)}
        return {"content": reply}

    mock = AsyncMock(side_effect=model)
    result = await rewrite_assist_draft(
        SimpleNamespace(run_supervisor_agent=mock), project_id="project",
        agent_id=None, customer_message=question,
        factual_draft=factual_draft or reply, knowledge_evidence=evidence(content),
        recent_messages=recent_messages,
    )
    return result, mock


@pytest.mark.asyncio
async def test_screenshot_question_accepts_verified_calculations():
    reply = (
        "按980元兑3000积分这档补充充值估算，每天只生成10篇消耗20积分，"
        "1000元约能用153天；如果每天还发布这10篇，约能用102天。"
    )
    inputs = geo_inputs()
    calculations = [
        proof("daily * generate", "20", "20积分", [inputs[3], inputs[4]]),
        proof("budget / price * credits / (daily * generate)", "153", "153天",
              inputs, "floor"),
        proof("budget / price * credits / (daily * (generate + publish))",
              "102", "102天", inputs + [operand("publish", "1", "发布一篇文章扣1积分")],
              "floor"),
    ]
    result, calls = await rewrite(reply, calculations)
    assert result == reply
    assert "fact_audit" in [c.kwargs.get("trace_phase") for c in calls.await_args_list]


@pytest.mark.asyncio
async def test_actual_voice_transcription_accepts_chinese_quantities():
    question = "那如果我每天生成十篇文章的话，我大概一千块钱能用多久？"
    inputs = geo_inputs()
    inputs[2]["quote"] = "一千块钱"
    inputs[3]["quote"] = "每天生成十篇文章"
    reply = "按980元兑3000积分这档补充充值估算，只生成文章约能用153天。"
    result, _ = await rewrite(reply, [proof(
        "budget / price * credits / (daily * generate)", "153", "153天", inputs, "floor",
    )], question=question)
    assert result == reply


@pytest.mark.asyncio
async def test_shared_inputs_do_not_authorize_unused_numbers():
    inputs = geo_inputs()
    result, _ = await rewrite("每天消耗20积分。", [proof(
        "daily * generate", "20", "20积分", inputs,
    )])
    assert result == "每天消耗20积分。"
    # An extra cited input is not a blanket exemption for other new quantities.
    result, _ = await rewrite("每天消耗20积分，赠送999积分。", [proof(
        "daily * generate", "20", "20积分", inputs,
    )])
    assert result == UNCONFIRMED_REPLY


@pytest.mark.asyncio
async def test_percentage_uses_only_the_fixed_conversion_constant():
    reply = "按退费比例估算，可退240元。"
    result, _ = await rewrite(reply, [proof(
        "amount * rate / percent", "240", "240元", [
            operand("amount", "300", "已付300元", "customer:current"),
            operand("rate", "80", "退回已付金额的80%"),
            operand("percent", "100", "百分比换算基数为100", "constant:percent"),
        ],
    )], content="满足退费条件时退回已付金额的80%。", question="我已付300元，满足退费条件，能退多少？")
    assert result == reply


@pytest.mark.asyncio
@pytest.mark.parametrize("calculations", [[], [proof(
    "budget / price * credits / (daily * generate)", "200", "200天", geo_inputs(), "floor",
)]])
async def test_new_number_without_correct_arithmetic_still_fails_closed(calculations):
    result, _ = await rewrite("按980元兑3000积分这档估算，可用200天。", calculations)
    assert result == UNCONFIRMED_REPLY


@pytest.mark.asyncio
async def test_one_valid_calculation_does_not_authorize_other_numbers():
    inputs = geo_inputs()
    result, _ = await rewrite("每天消耗20积分，另送999积分。", [proof(
        "daily * generate", "20", "20积分", [inputs[3], inputs[4]],
    )])
    assert result == UNCONFIRMED_REPLY


@pytest.mark.asyncio
async def test_claim_may_repeat_proven_inputs_but_cannot_hide_a_second_result():
    inputs = [geo_inputs()[3], geo_inputs()[4]]
    for claim, expected in [
        ("每天10篇消耗20积分", "每天10篇消耗20积分。"),
        ("每天10篇消耗20积分另送999积分", UNCONFIRMED_REPLY),
    ]:
        result, _ = await rewrite(claim + "。", [proof("daily * generate", "20", claim, inputs)])
        assert result == expected


@pytest.mark.asyncio
async def test_log_records_only_issue_codes(caplog):
    await rewrite("每天消耗20积分。", [], valid=False,
                  issues=["unsupported_claim: confidential customer text"])
    records = [r for r in caplog.records if r.message == "Customer reply validation rejected"]
    assert records
    assert all(r.issues == ["unsupported_claim"] for r in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("source_id,quote", [
    ("document:other", "生成一篇文章扣2积分"),
    ("document:policy", "生成一篇文章只扣2积分"),
    ("draft", "生成一篇文章扣2积分"),
])
async def test_operands_must_point_to_exact_current_sources(source_id, quote):
    inputs = [geo_inputs()[3], operand("generate", "2", quote, source_id)]
    result, _ = await rewrite("每天消耗20积分。", [proof(
        "daily * generate", "20", "20积分", inputs,
    )])
    assert result == UNCONFIRMED_REPLY


@pytest.mark.asyncio
async def test_result_cannot_be_laundered_through_a_numeric_substring():
    result, _ = await rewrite("充值后可以用1530天。", [proof(
        "budget / price * credits / (daily * generate)", "153", "153",
        geo_inputs(), "floor",
    )])
    assert result == UNCONFIRMED_REPLY


@pytest.mark.asyncio
async def test_correct_math_cannot_override_semantic_tier_or_unit_rejection():
    result, _ = await rewrite("任何套餐充值1000元都能用153天。", [proof(
        "budget / price * credits / (daily * generate)", "153", "153天",
        geo_inputs(), "floor",
    )], valid=False, issues=["unsupported_universal_tier"])
    assert result == UNCONFIRMED_REPLY


@pytest.mark.asyncio
async def test_generic_shipping_arithmetic_is_not_geo_specific():
    reply = "按首重和续重规则，这个包裹的运费是14元。"
    result, _ = await rewrite(reply, [proof(
        "base + (weight - first) * extra", "14", "14元", [
            operand("base", "8", "首重1千克8元"),
            operand("first", "1", "首重1千克8元"),
            operand("extra", "2", "续重每千克2元"),
            operand("weight", "4", "包裹重4千克", "customer:current"),
        ],
    )], content="首重1千克8元，续重每千克2元。", question="包裹重4千克，运费多少？")
    assert result == reply


@pytest.mark.asyncio
@pytest.mark.parametrize("expression", ["daily / (generate - generate)",
    "__import__('os').system('echo unsafe')", "daily ** generate", "daily + 123"])
async def test_unbound_literals_unsafe_expressions_and_division_by_zero_are_rejected(expression):
    result, _ = await rewrite("每天消耗20积分。", [proof(
        expression, "20", "20积分", [geo_inputs()[3], geo_inputs()[4]],
    )])
    assert result == UNCONFIRMED_REPLY


def test_original_numeric_gate_stays_strict_without_verified_proof():
    assert "new_numbers" in assess_reply(
        "每天消耗20积分，可用153天。", "每天消耗20积分，可用153天。", QUESTION,
        knowledge_evidence=evidence(), include_style=False,
    )


@pytest.mark.parametrize("source_valid", [True, False])
def test_underanswered_reply_gets_only_source_verified_calculation_feedback(source_valid):
    inputs = geo_inputs()
    if not source_valid:
        inputs[0]["source_id"] = "document:missing"
    hint = CalculationHint(inputs=inputs,
        expression="budget / price * credits / (daily * generate)",
        rounding="floor", label="可用天数", assumption="按已有基础档比例估算")
    feedback = calculation_feedback([hint], calculation_sources(evidence(), QUESTION, []), numeric_values)
    if source_valid:
        assert len(feedback) == 1
        data = json.loads(feedback[0].removeprefix("verified_calculation: "))
        assert data["result"] == "153"
    else:
        assert feedback == []


@pytest.mark.asyncio
async def test_missing_internal_proof_is_repaired_without_rewriting_the_answer():
    inputs = [geo_inputs()[3], geo_inputs()[4]]
    phases = []

    async def model(**kwargs):
        phases.append(kwargs["trace_phase"])
        if kwargs["trace_phase"] == "expression_rewrite":
            return {"content": "每天消耗20积分。"}
        calculations = [] if len(phases) == 2 else [proof(
            "daily * generate", "20", "20积分", inputs,
        )]
        return {"content": json.dumps({"valid":True,"issues":[],"calculations":calculations})}

    result = await rewrite_assist_draft(SimpleNamespace(run_supervisor_agent=AsyncMock(side_effect=model)),
        project_id="project", agent_id=None, customer_message=QUESTION,
        factual_draft="每天消耗20积分。", knowledge_evidence=evidence())
    assert result == "每天消耗20积分。"
    assert phases == ["expression_rewrite", "fact_audit", "fact_audit"]


@pytest.mark.asyncio
async def test_each_repeated_derived_quantity_is_requested_and_verified_separately():
    reply = "每天消耗20积分，后续按每天20积分估算。"
    inputs = [geo_inputs()[3], geo_inputs()[4]]

    async def model(**kwargs):
        if kwargs["trace_phase"] == "expression_rewrite":
            return {"content": reply}
        request = json.loads(kwargs["message"])
        positions = request.get("必须逐处证明的位置", [])
        calculations = [proof("daily * generate", "20", "20积分", inputs)
                        for _ in positions]
        return {"content": json.dumps({"valid": True, "issues": [],
                                      "calculations": calculations})}

    result = await rewrite_assist_draft(
        SimpleNamespace(run_supervisor_agent=AsyncMock(side_effect=model)),
        project_id="project", agent_id=None, customer_message=QUESTION,
        factual_draft=reply, knowledge_evidence=evidence())
    assert result == reply


@pytest.mark.asyncio
async def test_proof_retry_receives_program_computed_result_without_waiving_checks():
    reply = "按已有基础档比例估算，1000元每天只生成10篇可用约153天。"
    calls = []

    async def model(**kwargs):
        calls.append(kwargs["trace_phase"])
        if kwargs["trace_phase"] == "expression_rewrite":
            return {"content": reply}
        request = json.loads(kwargs["message"])
        references = request.get("程序复算参考", [])
        rounding = "floor" if references else "exact"
        if references:
            assert any('"result": "153"' in item for item in references)
        return {"content": json.dumps({"valid": True, "issues": [],
            "calculations": [proof("budget / price * credits / (daily * generate)",
                                   "153", "153天", geo_inputs(), rounding)]})}

    result = await rewrite_assist_draft(
        SimpleNamespace(run_supervisor_agent=AsyncMock(side_effect=model)),
        project_id="project", agent_id=None, customer_message=QUESTION,
        factual_draft=reply, knowledge_evidence=evidence())
    assert result == reply
    assert calls == ["expression_rewrite", "fact_audit", "fact_audit"]


@pytest.mark.asyncio
async def test_underanswered_reply_repairs_using_computed_data_and_still_gets_a_final_audit():
    inputs = geo_inputs()
    hint = {"inputs": inputs, "expression": "budget / price * credits / (daily * generate)",
            "rounding": "floor", "label": "可用天数", "assumption": "按980元档比例估算"}
    final_reply = "按980元兑3000积分档的比例估算，每天只生成10篇，1000元约能用153天。"
    phases = []

    async def model(**kwargs):
        phase = kwargs["trace_phase"]
        phases.append(phase)
        if phases == ["expression_rewrite"]:
            return {"content": "具体积分尚不能确认，请先确定充值档位。"}
        if phases == ["expression_rewrite", "fact_audit"]:
            return {"content": json.dumps({"valid":False,"issues":["answer_incomplete"],
                                          "calculation_hints":[hint]}, ensure_ascii=False)}
        if phase == "expression_rewrite":
            assert 'verified_calculation:' in kwargs["system_message"]
            assert '"result": "153"' in kwargs["system_message"]
            return {"content": final_reply}
        return {"content": json.dumps({"valid":True,"issues":[],"calculations":[proof(
            hint["expression"], "153", "153天", inputs, "floor",
        )]}, ensure_ascii=False)}

    result = await rewrite_assist_draft(SimpleNamespace(run_supervisor_agent=AsyncMock(side_effect=model)),
        project_id="project", agent_id=None, customer_message=QUESTION,
        factual_draft="需要先确认档位。", knowledge_evidence=evidence())
    assert result == final_reply
    assert phases == ["expression_rewrite", "fact_audit", "expression_rewrite", "fact_audit"]
