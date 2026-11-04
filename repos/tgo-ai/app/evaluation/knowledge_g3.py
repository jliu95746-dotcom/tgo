"""Offline gate for human-reviewed knowledge answers from one fixed release.

Reviewers supply deidentified golden cases and verdicts as JSONL. This module
does not call a model or publish the case text in its aggregate report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)
from app.services.sensitive_data_redactor import SensitiveDataRedactor

KnowledgeCategory = Literal["product", "aftersales", "faq", "sop"]
AnswerBehavior = Literal["answer", "handoff"]
ReviewVerdict = Literal["pass", "fail", "unsafe"]

MIN_CASES = 40
MIN_CASES_PER_CATEGORY = 5
MIN_PASS_RATE = 0.90
MAX_CASES = 1000
MAX_DATASET_BYTES = 16 * 1024 * 1024
CATEGORIES: tuple[KnowledgeCategory, ...] = (
    "product",
    "aftersales",
    "faq",
    "sop",
)


class GoldenKnowledgeCase(BaseModel):
    """One deidentified question and its human-approved expected outcome."""

    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"
    )
    category: KnowledgeCategory
    question: str = Field(min_length=1, max_length=2000)
    expected_behavior: AnswerBehavior
    expected_answer: str | None = Field(default=None, max_length=4000)
    expected_source_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_reference(self) -> GoldenKnowledgeCase:
        if self.expected_behavior == "answer":
            if not self.expected_answer or not self.expected_source_ids:
                raise ValueError("answer cases need a reference and source")
        elif self.expected_source_ids:
            raise ValueError("handoff cases cannot require answer sources")
        if len(set(self.expected_source_ids)) != len(self.expected_source_ids):
            raise ValueError("duplicate reference source")
        if any(
            not _valid_source_id(value) for value in self.expected_source_ids
        ):
            raise ValueError("invalid reference source")
        return self


class ReviewedKnowledgeAnswer(BaseModel):
    """Human verdict; answer text remains outside aggregate reports."""

    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"
    )
    batch_id: UUID
    execution_id: UUID
    release_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    answer_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_behavior: AnswerBehavior
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    verdict: ReviewVerdict

    @model_validator(mode="after")
    def require_valid_sources(self) -> ReviewedKnowledgeAnswer:
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("duplicate observed source")
        if any(not _valid_source_id(value) for value in self.source_ids):
            raise ValueError("invalid observed source")
        return self


class CategoryMetrics(BaseModel):
    category: KnowledgeCategory
    sample_count: int
    correct_count: int
    pass_rate: float


class KnowledgeEvaluationReport(BaseModel):
    sample_count: int
    correct_count: int
    pass_rate: float
    unsafe_count: int
    behavior_mismatch_count: int
    source_mismatch_count: int
    per_category: tuple[CategoryMetrics, ...]
    issues: tuple[str, ...]
    passed: bool
    batch_id: UUID
    release_digest: str
    dataset_sha256: str | None = None
    reviews_sha256: str | None = None


@dataclass(frozen=True)
class LoadedCases:
    records: tuple[GoldenKnowledgeCase, ...]
    sha256: str


@dataclass(frozen=True)
class LoadedReviews:
    records: tuple[ReviewedKnowledgeAnswer, ...]
    sha256: str


def _valid_source_id(value: str) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and all(
            character.isascii()
            and (character.isalnum() or character in "._:/-")
            for character in value
        )
    )


def _texts_contain_personal_data(texts: Sequence[str | None]) -> bool:
    """Reject obvious identifiers; human review must still assess anonymity."""
    redactor = SensitiveDataRedactor()
    return any(
        redactor.redact(text).categories
        for text in texts
        if text
    )


def _contains_detectable_personal_data(case: GoldenKnowledgeCase) -> bool:
    return _texts_contain_personal_data((
        case.case_id, case.question, case.expected_answer,
        *case.expected_source_ids,
    ))


def _review_contains_personal_data(review: ReviewedKnowledgeAnswer) -> bool:
    return _texts_contain_personal_data((
        review.case_id, *review.source_ids,
    ))


def _read_jsonl(path: Path, label: str) -> tuple[list[str], str]:
    try:
        if not path.is_file() or path.stat().st_size > MAX_DATASET_BYTES:
            raise ValueError(f"{label} dataset is missing or too large")
        content = path.read_bytes()
        if len(content) > MAX_DATASET_BYTES:
            raise ValueError(f"{label} dataset is missing or too large")
        lines = content.decode("utf-8").splitlines()
    except (OSError, UnicodeError):
        raise ValueError(f"{label} dataset cannot be read") from None
    if not 1 <= len(lines) <= MAX_CASES:
        raise ValueError(f"{label} dataset has an invalid record count")
    return lines, hashlib.sha256(content).hexdigest()


def load_cases(path: Path) -> LoadedCases:
    """Reject extra fields and keep question text out of errors."""
    lines, digest = _read_jsonl(path, "case")
    records: list[GoldenKnowledgeCase] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        try:
            record = GoldenKnowledgeCase.model_validate_json(line)
        except (ValidationError, ValueError):
            raise ValueError(f"invalid case on line {line_number}") from None
        if _contains_detectable_personal_data(record):
            raise ValueError(f"detectable personal data on line {line_number}")
        if record.case_id in seen_ids:
            raise ValueError(f"duplicate case id on line {line_number}")
        seen_ids.add(record.case_id)
        records.append(record)
    return LoadedCases(tuple(records), digest)


def load_reviews(path: Path) -> LoadedReviews:
    """Read only the reviewed answer projection, not actual answer content."""
    lines, digest = _read_jsonl(path, "review")
    records: list[ReviewedKnowledgeAnswer] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        try:
            record = ReviewedKnowledgeAnswer.model_validate_json(line)
        except (ValidationError, ValueError):
            raise ValueError(f"invalid review on line {line_number}") from None
        if _review_contains_personal_data(record):
            raise ValueError(f"detectable personal data on line {line_number}")
        if record.case_id in seen_ids:
            raise ValueError(f"duplicate review case id on line {line_number}")
        seen_ids.add(record.case_id)
        records.append(record)
    return LoadedReviews(tuple(records), digest)


def evaluate_knowledge(
    cases: Sequence[GoldenKnowledgeCase],
    reviews: Sequence[ReviewedKnowledgeAnswer],
) -> KnowledgeEvaluationReport:
    """Score one complete reviewed batch; fail closed on unsafe answers."""
    for index, case in enumerate(cases, start=1):
        if _contains_detectable_personal_data(case):
            raise ValueError(f"detectable personal data in case {index}")
    for index, review in enumerate(reviews, start=1):
        if _review_contains_personal_data(review):
            raise ValueError(f"detectable personal data in review {index}")
    case_ids = [case.case_id for case in cases]
    review_ids = [review.case_id for review in reviews]
    if len(case_ids) != len(set(case_ids)) or set(case_ids) != set(review_ids):
        raise ValueError("golden and reviewed case ids do not align")
    if len(review_ids) != len(set(review_ids)):
        raise ValueError("duplicate reviewed case ids")
    if not cases:
        raise ValueError("golden and reviewed case ids do not align")
    if len({review.batch_id for review in reviews}) != 1:
        raise ValueError("mixed batch in reviewed answers")
    if len({review.release_digest for review in reviews}) != 1:
        raise ValueError("mixed release in reviewed answers")
    if len({review.execution_id for review in reviews}) != len(reviews):
        raise ValueError("duplicate execution in reviewed answers")

    reviews_by_id = {review.case_id: review for review in reviews}
    support: Counter[KnowledgeCategory] = Counter()
    correct_by_category: Counter[KnowledgeCategory] = Counter()
    behavior_mismatch_count = 0
    source_mismatch_count = 0
    unsafe_count = 0
    for case in cases:
        review = reviews_by_id[case.case_id]
        support[case.category] += 1
        behavior_matches = case.expected_behavior == review.observed_behavior
        source_matches = case.expected_behavior == "handoff" or (
            bool(review.source_ids)
            and set(review.source_ids).issubset(case.expected_source_ids)
        )
        behavior_mismatch_count += int(not behavior_matches)
        source_mismatch_count += int(not source_matches)
        unsafe_count += int(review.verdict == "unsafe")
        if review.verdict == "pass" and behavior_matches and source_matches:
            correct_by_category[case.category] += 1

    per_category = tuple(
        CategoryMetrics(
            category=category,
            sample_count=support[category],
            correct_count=correct_by_category[category],
            pass_rate=(
                correct_by_category[category] / support[category]
                if support[category]
                else 0.0
            ),
        )
        for category in CATEGORIES
    )
    correct_count = sum(correct_by_category.values())
    pass_rate = correct_count / len(cases)
    issues: list[str] = []
    if len(cases) < MIN_CASES:
        issues.append("insufficient_cases")
    if any(
        support[category] < MIN_CASES_PER_CATEGORY for category in CATEGORIES
    ):
        issues.append("insufficient_category_coverage")
    if not any(case.expected_behavior == "handoff" for case in cases):
        issues.append("missing_handoff_case")
    if pass_rate < MIN_PASS_RATE:
        issues.append("pass_rate_below_target")
    if unsafe_count:
        issues.append("unsafe_answer")
    first_review = reviews[0]
    return KnowledgeEvaluationReport(
        sample_count=len(cases),
        correct_count=correct_count,
        pass_rate=pass_rate,
        unsafe_count=unsafe_count,
        behavior_mismatch_count=behavior_mismatch_count,
        source_mismatch_count=source_mismatch_count,
        per_category=per_category,
        issues=tuple(issues),
        passed=not issues,
        batch_id=first_review.batch_id,
        release_digest=first_review.release_digest,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate or score reviewed knowledge cases"
    )
    parser.add_argument(
        "--cases", required=True, type=Path, help="deidentified golden JSONL"
    )
    parser.add_argument("--reviews", type=Path, help="reviewed answer JSONL")
    arguments = parser.parse_args(argv)
    cases = load_cases(arguments.cases)
    if arguments.reviews is None:
        print(
            json.dumps(
                {
                    "validated_cases": len(cases.records),
                    "dataset_sha256": cases.sha256,
                },
                ensure_ascii=False,
            )
        )
        return 0
    reviews = load_reviews(arguments.reviews)
    report = evaluate_knowledge(cases.records, reviews.records).model_copy(
        update={
            "dataset_sha256": cases.sha256,
            "reviews_sha256": reviews.sha256,
        }
    )
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
