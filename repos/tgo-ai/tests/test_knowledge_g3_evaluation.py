"""Golden knowledge answer gate uses one reviewed, fixed-release batch."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.evaluation.knowledge_g3 import (
    GoldenKnowledgeCase,
    ReviewedKnowledgeAnswer,
    evaluate_knowledge,
    load_cases,
    load_reviews,
    main,
)


CATEGORIES = ("product", "aftersales", "faq", "sop")
DIGEST = "a" * 64


def _batch() -> (
    tuple[tuple[GoldenKnowledgeCase, ...], tuple[ReviewedKnowledgeAnswer, ...]]
):
    batch_id = uuid4()
    cases = tuple(
        GoldenKnowledgeCase(
            case_id=f"case-{index}",
            category=CATEGORIES[index % len(CATEGORIES)],
            question=f"脱敏问题 {index}",
            expected_behavior="handoff" if index == 0 else "answer",
            expected_answer=None if index == 0 else f"人工审定答案 {index}",
            expected_source_ids=[] if index == 0 else [f"source-{index}"],
        )
        for index in range(40)
    )
    reviews = tuple(
        ReviewedKnowledgeAnswer(
            case_id=case.case_id,
            batch_id=batch_id,
            execution_id=uuid4(),
            release_digest=DIGEST,
            answer_digest="b" * 64,
            observed_behavior=case.expected_behavior,
            source_ids=case.expected_source_ids,
            verdict="pass",
        )
        for case in cases
    )
    return cases, reviews


def test_reviewed_balanced_batch_passes_ninety_percent_gate() -> None:
    cases, reviews = _batch()
    report = evaluate_knowledge(cases, reviews)
    assert report.passed is True
    assert report.pass_rate == 1.0
    assert report.sample_count == 40
    assert len(report.per_category) == 4


def test_below_target_or_unsafe_answer_fails_closed() -> None:
    cases, reviews = _batch()
    low = tuple(
        review.model_copy(update={"verdict": "fail"}) if index < 5 else review
        for index, review in enumerate(reviews)
    )
    report = evaluate_knowledge(cases, low)
    assert report.pass_rate == 0.875
    assert "pass_rate_below_target" in report.issues
    unsafe = list(reviews)
    unsafe[1] = unsafe[1].model_copy(update={"verdict": "unsafe"})
    report = evaluate_knowledge(cases, tuple(unsafe))
    assert report.passed is False
    assert "unsafe_answer" in report.issues


def test_positive_review_cannot_mask_wrong_route_or_missing_source() -> None:
    cases, reviews = _batch()
    changed = list(reviews)
    changed[0] = changed[0].model_copy(update={"observed_behavior": "answer"})
    changed[1] = changed[1].model_copy(update={"source_ids": []})
    report = evaluate_knowledge(cases, tuple(changed))
    assert report.correct_count == 38
    assert report.behavior_mismatch_count == 1
    assert report.source_mismatch_count == 1


def test_positive_review_cannot_cite_an_unapproved_source() -> None:
    cases, reviews = _batch()
    changed = list(reviews)
    changed[1] = changed[1].model_copy(
        update={"source_ids": ["source-1", "unapproved-source"]}
    )
    report = evaluate_knowledge(cases, tuple(changed))
    assert report.correct_count == 39
    assert report.source_mismatch_count == 1


def test_mixed_release_and_misaligned_runs_are_rejected() -> None:
    cases, reviews = _batch()
    changed = list(reviews)
    changed[1] = changed[1].model_copy(update={"release_digest": "c" * 64})
    with pytest.raises(ValueError, match="mixed release"):
        evaluate_knowledge(cases, tuple(changed))
    with pytest.raises(ValueError, match="case ids"):
        evaluate_knowledge(cases, reviews[:-1])


def test_duplicate_execution_and_weak_coverage_fail_closed() -> None:
    cases, reviews = _batch()
    duplicate = list(reviews)
    duplicate[1] = duplicate[1].model_copy(
        update={"execution_id": duplicate[0].execution_id}
    )
    with pytest.raises(ValueError, match="duplicate execution"):
        evaluate_knowledge(cases, tuple(duplicate))

    short = evaluate_knowledge(cases[:20], reviews[:20])
    assert short.passed is False
    assert "insufficient_cases" in short.issues


def test_cli_reports_only_aggregate_evidence_and_rejects_raw_extras(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases, reviews = _batch()
    case_path = tmp_path / "cases.jsonl"
    review_path = tmp_path / "reviews.jsonl"
    case_path.write_text(
        "\n".join(
            json.dumps(case.model_dump(mode="json"), ensure_ascii=False)
            for case in cases
        )
        + "\n",
        encoding="utf-8",
    )
    review_path.write_text(
        "\n".join(
            json.dumps(review.model_dump(mode="json")) for review in reviews
        )
        + "\n",
        encoding="utf-8",
    )
    assert main(["--cases", str(case_path)]) == 0
    assert (
        main(["--cases", str(case_path), "--reviews", str(review_path)]) == 0
    )
    output = capsys.readouterr().out
    assert "脱敏问题" not in output
    assert "人工审定答案" not in output
    assert '"passed": true' in output

    invalid = cases[0].model_dump(mode="json")
    invalid["private_email"] = "private@example.com"
    case_path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid case on line 1") as error:
        load_cases(case_path)
    assert "private@example.com" not in str(error.value)
    assert len(load_reviews(review_path).records) == 40

    raw_review = reviews[0].model_dump(mode="json")
    raw_review["answer_text"] = "private@example.com"
    review_path.write_text(json.dumps(raw_review) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid review on line 1") as error:
        load_reviews(review_path)
    assert "private@example.com" not in str(error.value)


@pytest.mark.parametrize(
    "field,sensitive_text",
    [
        ("question", "请联系 demo@example.test 处理售后"),
        ("expected_answer", "请发送到 demo@example.test"),
        ("case_id", "139" + "0" * 8),
        ("expected_source_ids", ["139" + "0" * 8]),
    ],
    ids=["question", "answer", "case-id", "source-id"],
)
def test_case_file_rejects_detectable_personal_data_without_echo(
    tmp_path: Path,
    field: str,
    sensitive_text: str | list[str],
) -> None:
    cases, _ = _batch()
    first = cases[1].model_dump(mode="json")
    first[field] = sensitive_text
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="personal data on line 1") as error:
        load_cases(path)
    assert "example.test" not in str(error.value)
    assert "13900000000" not in str(error.value)


def test_direct_knowledge_score_rejects_sensitive_case() -> None:
    cases, reviews = _batch()
    changed = list(cases)
    changed[-1] = changed[-1].model_copy(
        update={"question": "我的邮箱 demo@example.test 如何修改？"}
    )
    with pytest.raises(ValueError, match="personal data in case 40") as error:
        evaluate_knowledge(tuple(changed), reviews)
    assert "example.test" not in str(error.value)


def test_review_file_and_direct_score_reject_sensitive_source_id(
    tmp_path: Path,
) -> None:
    cases, reviews = _batch()
    private_source = "139" + "0" * 8
    raw = reviews[1].model_dump(mode="json")
    raw["source_ids"] = [private_source]
    path = tmp_path / "reviews.jsonl"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="personal data on line 1") as error:
        load_reviews(path)
    assert private_source not in str(error.value)

    changed = list(reviews)
    changed[1] = changed[1].model_copy(
        update={"source_ids": [private_source]}
    )
    with pytest.raises(ValueError, match="personal data in review 2"):
        evaluate_knowledge(cases, tuple(changed))
