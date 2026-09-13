"""Natural uncertainty must not be matched as confirmed product absence."""

import pytest

from app.schemas.humanization import TrainingExample
from app.services.humanization_matching import match_examples, result_state


@pytest.mark.parametrize("facts", [
    "绿色法棍包现在确认不了有没有。",
    "绿色现在还确认不了。",
    "现在不能确定有没有绿色。",
    "现在无法确定有没有绿色。",
    "绿色有没有还说不准。",
    "有没有绿色还不清楚。",
    "有没有绿色，现在还不确定。",
    "是否有绿色，目前尚未确认。",
])
def test_colloquial_uncertainty_has_unknown_result_state(facts: str) -> None:
    assert result_state(facts) == "unknown"


@pytest.mark.parametrize("facts", ["有没有绿色？", "这款有没有绿色需要核对。"])
def test_availability_question_is_not_confirmed_absence(facts: str) -> None:
    assert result_state(facts) != "absent"


@pytest.mark.parametrize("facts,expected", [
    ("已确认这款没有绿色。", "absent"),
    ("没有绿色现货。", "absent"),
    ("已确认有绿色。", "available"),
    ("未找到绿色款，是否有还不能确认。", "not_found"),
    ("查询失败，颜色未能确认。", "failed"),
])
def test_existing_result_states_remain_distinct(facts: str, expected: str) -> None:
    assert result_state(facts) == expected


@pytest.mark.parametrize("facts,expected", [
    ("这款法棍包是否有绿色，目前尚未确认。", ["unknown"]),
    ("已确认这款法棍包没有绿色。", ["absent"]),
])
def test_matching_uses_uncertainty_not_the_substring_in_you_mei_you(
    facts: str, expected: list[str],
) -> None:
    samples = [
        TrainingExample(
            id="unknown", customer_message="绿色的法棍包有吗？",
            ai_draft="绿色法棍包现在确认不了有没有。",
            final_reply="有没有绿色的法棍包，现在还不确定。",
            change_kind="expression",
        ),
        TrainingExample(
            id="absent", customer_message="绿色的法棍包有吗？",
            ai_draft="这款没有绿色。", final_reply="没有绿色。",
            change_kind="expression",
        ),
    ]
    result = match_examples(samples, "绿色的法棍包有吗？", facts)
    assert [sample.id for sample in result] == expected
