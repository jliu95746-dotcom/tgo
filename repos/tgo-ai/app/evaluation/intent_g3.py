"""Evaluate the original G3 intent gates against deidentified historical cases.

The live runner calls the existing authenticated intent endpoint. It never
prints or stores customer text in its aggregate report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, Sequence
from uuid import UUID

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.runtime.routing.policy import IntentRoutingPolicy
from app.schemas.intent import (
    IntentClassificationInput,
    IntentClassificationResult,
    IntentName,
    IntentRoute,
)
from app.services.sensitive_data_redactor import SensitiveDataRedactor

MIN_CASES = 200
HIGH_CONFIDENCE_THRESHOLD = IntentRoutingPolicy.HIGH_CONFIDENCE_THRESHOLD
MIN_HIGH_CONFIDENCE_PRECISION = 0.95
MIN_MACRO_F1 = 0.85
MAX_DATASET_BYTES = 16 * 1024 * 1024
MAX_CASES = 1000
SENSITIVE_INTENTS = frozenset({IntentName.COMPLAINT, IntentName.HUMAN_HANDOFF})


class GoldenCase(BaseModel):
    """One deidentified case with a human-reviewed reference label."""

    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    expected_intent: IntentName
    requires_handoff: bool
    classification_input: IntentClassificationInput


class ObservedPrediction(BaseModel):
    """PII-free projection of one already-persisted classification result."""

    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    intent: IntentName
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    recommended_route: IntentRoute
    need_human: bool
    taxonomy_version: Literal["v1"]
    classifier_version: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$"
    )
    policy_version: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$"
    )

    @model_validator(mode="after")  # type: ignore[misc]
    def require_consistent_handoff(self) -> Self:
        if self.need_human != (self.recommended_route is IntentRoute.HUMAN_HANDOFF):
            raise ValueError("handoff flag and route disagree")
        return self


class EvaluationTarget(BaseModel):
    """One project-owned provider selected for the existing analysis API."""

    model_config = ConfigDict(extra="forbid", strict=True)

    base_url: str
    project_id: UUID
    provider_id: UUID
    model: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$")

    @field_validator("base_url")  # type: ignore[misc]
    @classmethod
    def require_safe_base_url(cls, value: str) -> str:
        try:
            url = httpx.URL(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid evaluation base URL") from exc
        if (
            not url.host
            or url.userinfo
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError("invalid evaluation base URL")
        if url.scheme == "http" and url.host not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("remote evaluation requires HTTPS")
        if url.scheme not in {"http", "https"}:
            raise ValueError("evaluation requires HTTP or HTTPS")
        return value.rstrip("/")


class ClassMetrics(BaseModel):
    intent: IntentName
    support: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float


class EvaluationReport(BaseModel):
    sample_count: int
    high_confidence_count: int
    high_confidence_precision: float
    macro_f1: float
    sensitive_case_count: int
    sensitive_handoff_rate: float
    per_class: tuple[ClassMetrics, ...]
    issues: tuple[str, ...]
    passed: bool
    dataset_sha256: str | None = None
    prediction_sha256: str | None = None
    model: str | None = None
    classifier_version: str | None = None
    policy_version: str | None = None


class EvaluationRunError(RuntimeError):
    """Safe error text: no customer payload or provider response body."""


@dataclass(frozen=True)
class LoadedDataset:
    cases: tuple[GoldenCase, ...]
    sha256: str


@dataclass(frozen=True)
class LoadedPredictions:
    predictions: tuple[ObservedPrediction, ...]
    sha256: str


def load_cases(path: Path) -> LoadedDataset:
    """Read JSONL without allowing raw fields into reports or diagnostics."""
    if not path.is_file() or path.stat().st_size > MAX_DATASET_BYTES:
        raise ValueError("evaluation dataset is missing or too large")
    try:
        content = path.read_bytes()
        lines = content.decode("utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("evaluation dataset cannot be read as UTF-8") from exc
    if not 1 <= len(lines) <= MAX_CASES:
        raise ValueError("evaluation dataset has an invalid case count")
    cases: list[GoldenCase] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        try:
            case = GoldenCase.model_validate_json(line)
        except (ValidationError, ValueError) as exc:
            raise ValueError(f"invalid record on line {line_number}") from exc
        if _contains_detectable_personal_data(case):
            raise ValueError(f"detectable personal data on line {line_number}")
        if case.case_id in seen_ids:
            raise ValueError(f"duplicate case_id on line {line_number}")
        seen_ids.add(case.case_id)
        cases.append(case)
    return LoadedDataset(tuple(cases), hashlib.sha256(content).hexdigest())


def load_predictions(path: Path) -> LoadedPredictions:
    """Read previously observed results without accepting customer content."""
    if not path.is_file() or path.stat().st_size > MAX_DATASET_BYTES:
        raise ValueError("prediction dataset is missing or too large")
    try:
        content = path.read_bytes()
        lines = content.decode("utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("prediction dataset cannot be read as UTF-8") from exc
    if not 1 <= len(lines) <= MAX_CASES:
        raise ValueError("prediction dataset has an invalid case count")
    predictions: list[ObservedPrediction] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        try:
            prediction = ObservedPrediction.model_validate_json(line)
        except (ValidationError, ValueError) as exc:
            raise ValueError(f"invalid prediction on line {line_number}") from exc
        if prediction.case_id in seen_ids:
            raise ValueError(f"duplicate prediction case_id on line {line_number}")
        seen_ids.add(prediction.case_id)
        predictions.append(prediction)
    return LoadedPredictions(tuple(predictions), hashlib.sha256(content).hexdigest())


def validate_prediction_versions(
    predictions: Sequence[ObservedPrediction],
) -> tuple[str, str]:
    versions = {
        (prediction.classifier_version, prediction.policy_version)
        for prediction in predictions
    }
    if len(versions) != 1:
        raise ValueError("mixed prediction versions")
    return next(iter(versions))


def align_predictions(
    cases: Sequence[GoldenCase], predictions: Sequence[ObservedPrediction]
) -> tuple[ObservedPrediction, ...]:
    by_id = {prediction.case_id: prediction for prediction in predictions}
    if (
        len(by_id) != len(predictions)
        or len(predictions) != len(cases)
        or set(by_id) != {case.case_id for case in cases}
    ):
        raise ValueError("prediction case ids do not match gold cases")
    return tuple(by_id[case.case_id] for case in cases)


async def run_cases(
    cases: Sequence[GoldenCase],
    target: EvaluationTarget,
    internal_key: str,
    client: httpx.AsyncClient,
) -> tuple[IntentClassificationResult, ...]:
    """Run every case once; any failure invalidates the entire evaluation."""
    if not internal_key:
        raise EvaluationRunError("internal evaluation key is missing")
    for index, case in enumerate(cases, start=1):
        if _contains_detectable_personal_data(case):
            raise EvaluationRunError(
                f"evaluation input {index} contains detectable personal data"
            )
    predictions: list[IntentClassificationResult] = []
    headers = {
        "X-Internal-API-Key": internal_key,
        "X-Project-Id": str(target.project_id),
    }
    for index, case in enumerate(cases, start=1):
        request_body = {
            "provider_id": str(target.provider_id),
            "model": target.model,
            "classification_input": case.classification_input.model_dump(
                mode="json", exclude_none=True
            ),
        }
        try:
            response = await client.post(
                target.base_url + "/api/v1/analysis/intent",
                headers=headers,
                json=request_body,
                timeout=30.0,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise EvaluationRunError(
                f"evaluation request {index} failed ({type(exc).__name__})"
            ) from None
        if response.status_code != 200:
            raise EvaluationRunError(
                f"evaluation request {index} failed with HTTP {response.status_code}"
            )
        try:
            predictions.append(
                IntentClassificationResult.model_validate_json(response.content)
            )
        except ValidationError:
            raise EvaluationRunError(
                f"evaluation request {index} returned an invalid result"
            ) from None
    return tuple(predictions)


def _contains_detectable_personal_data(case: GoldenCase) -> bool:
    """Reuse the customer-data patterns; this is a guard, not proof of anonymity."""
    redactor = SensitiveDataRedactor()
    content = case.classification_input
    return any(
        redactor.redact(text).categories
        for text in (
            case.case_id,
            content.user_text,
            content.asr_text,
            content.ocr_text,
            content.vlm_text,
        )
        if text
    )


def evaluate_cases(
    cases: Sequence[GoldenCase],
    predictions: Sequence[IntentClassificationResult | ObservedPrediction],
) -> EvaluationReport:
    """Calculate fixed-taxonomy metrics and fail closed on thin samples."""
    if not cases or len(cases) != len(predictions):
        raise ValueError("evaluation case and prediction counts must match")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("evaluation case ids must be unique")

    metrics: list[ClassMetrics] = []
    for name in IntentName:
        true_positives = sum(
            case.expected_intent is name and prediction.intent is name
            for case, prediction in zip(cases, predictions)
        )
        false_positives = sum(
            case.expected_intent is not name and prediction.intent is name
            for case, prediction in zip(cases, predictions)
        )
        false_negatives = sum(
            case.expected_intent is name and prediction.intent is not name
            for case, prediction in zip(cases, predictions)
        )
        precision = _ratio(true_positives, true_positives + false_positives)
        recall = _ratio(true_positives, true_positives + false_negatives)
        metrics.append(
            ClassMetrics(
                intent=name,
                support=true_positives + false_negatives,
                true_positives=true_positives,
                false_positives=false_positives,
                false_negatives=false_negatives,
                precision=precision,
                recall=recall,
                f1=_ratio(2 * precision * recall, precision + recall),
            )
        )

    high_confidence = [
        (case, prediction)
        for case, prediction in zip(cases, predictions)
        if prediction.confidence >= HIGH_CONFIDENCE_THRESHOLD
    ]
    high_precision = _ratio(
        sum(
            case.expected_intent is prediction.intent
            for case, prediction in high_confidence
        ),
        len(high_confidence),
    )
    sensitive = [
        prediction
        for case, prediction in zip(cases, predictions)
        if case.requires_handoff or case.expected_intent in SENSITIVE_INTENTS
    ]
    handoff_rate = _ratio(
        sum(
            prediction.need_human
            and prediction.recommended_route is IntentRoute.HUMAN_HANDOFF
            for prediction in sensitive
        ),
        len(sensitive),
    )
    macro_f1 = sum(item.f1 for item in metrics) / len(metrics)
    issues: list[str] = []
    if len(cases) < MIN_CASES:
        issues.append("insufficient_samples")
    if any(item.support == 0 for item in metrics):
        issues.append("missing_class_support")
    if not high_confidence:
        issues.append("no_high_confidence_cases")
    elif high_precision < MIN_HIGH_CONFIDENCE_PRECISION:
        issues.append("high_confidence_precision_below_target")
    if macro_f1 < MIN_MACRO_F1:
        issues.append("macro_f1_below_target")
    if not sensitive:
        issues.append("no_sensitive_cases")
    elif handoff_rate < 1.0:
        issues.append("sensitive_handoff_below_target")
    return EvaluationReport(
        sample_count=len(cases),
        high_confidence_count=len(high_confidence),
        high_confidence_precision=high_precision,
        macro_f1=macro_f1,
        sensitive_case_count=len(sensitive),
        sensitive_handoff_rate=handoff_rate,
        per_class=tuple(metrics),
        issues=tuple(issues),
        passed=not issues,
    )


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def validate_evaluation_coverage(cases: Sequence[GoldenCase]) -> None:
    """Reject incomplete gold sets before a potentially billable model run."""
    issues: list[str] = []
    if len(cases) < MIN_CASES:
        issues.append("insufficient_samples")
    if len({case.case_id for case in cases}) != len(cases):
        issues.append("duplicate_case_ids")
    if {case.expected_intent for case in cases} != set(IntentName):
        issues.append("missing_class_support")
    if not any(
        case.requires_handoff or case.expected_intent in SENSITIVE_INTENTS
        for case in cases
    ):
        issues.append("no_sensitive_cases")
    if issues:
        raise ValueError("evaluation coverage: " + ", ".join(issues))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate G3 intent gates")
    parser.add_argument("--input", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="call the configured model")
    mode.add_argument(
        "--predictions", type=Path, help="evaluate already-persisted results"
    )
    parser.add_argument("--base-url")
    parser.add_argument("--project-id", type=UUID)
    parser.add_argument("--provider-id", type=UUID)
    parser.add_argument("--model")
    args = parser.parse_args(argv)
    try:
        dataset = load_cases(args.input)
        cases = dataset.cases
        if not args.run and args.predictions is None:
            print(
                json.dumps(
                    {"validated_cases": len(cases), "dataset_sha256": dataset.sha256},
                    ensure_ascii=False,
                )
            )
            return 0
        validate_evaluation_coverage(cases)
        if args.predictions is not None:
            observed = load_predictions(args.predictions)
            classifier_version, policy_version = validate_prediction_versions(
                observed.predictions
            )
            aligned = align_predictions(cases, observed.predictions)
            historical_report = evaluate_cases(cases, aligned).model_copy(
                update={
                    "dataset_sha256": dataset.sha256,
                    "prediction_sha256": observed.sha256,
                    "classifier_version": classifier_version,
                    "policy_version": policy_version,
                }
            )
            print(historical_report.model_dump_json())
            return 0 if historical_report.passed else 1
        if not all((args.base_url, args.project_id, args.provider_id, args.model)):
            raise ValueError("live run requires base URL, project, provider, and model")
        internal_key = os.environ.get("TGO_INTENT_EVAL_INTERNAL_KEY", "")
        target = EvaluationTarget(
            base_url=args.base_url,
            project_id=args.project_id,
            provider_id=args.provider_id,
            model=args.model,
        )

        async def _run() -> tuple[IntentClassificationResult, ...]:
            async with httpx.AsyncClient(trust_env=False) as client:
                return await run_cases(cases, target, internal_key, client)

        predictions = asyncio.run(_run())
        report = evaluate_cases(cases, predictions).model_copy(
            update={"dataset_sha256": dataset.sha256, "model": target.model}
        )
        print(report.model_dump_json())
        return 0 if report.passed else 1
    except ValidationError:
        print("evaluation failed: invalid configuration", file=sys.stderr)
        return 2
    except OSError:
        print("evaluation failed: unable to read dataset", file=sys.stderr)
        return 2
    except (EvaluationRunError, ValueError) as exc:
        # Error messages in this module contain no input line or response body.
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
