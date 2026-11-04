"""G3 intent gate calculations and the existing authenticated analysis route."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.evaluation.intent_g3 import (
    EvaluationRunError,
    EvaluationTarget,
    GoldenCase,
    ObservedPrediction,
    evaluate_cases,
    load_cases,
    load_predictions,
    main,
    run_cases,
    validate_evaluation_coverage,
    validate_prediction_versions,
)
from app.schemas.intent import (
    ClassificationSource,
    IntentClassificationInput,
    IntentClassificationResult,
    IntentEntities,
    IntentName,
    IntentRoute,
    RiskLevel,
    RoutingReason,
)


def _cases(count: int = 200) -> tuple[GoldenCase, ...]:
    names = tuple(IntentName)
    return tuple(
        GoldenCase(
            case_id=f"case-{index}",
            expected_intent=names[index % len(names)],
            requires_handoff=names[index % len(names)]
            in {IntentName.COMPLAINT, IntentName.HUMAN_HANDOFF},
            classification_input=IntentClassificationInput(
                user_text=f"desensitized example {index}"
            ),
        )
        for index in range(count)
    )


def _prediction(
    intent: IntentName,
    *,
    confidence: float = 0.97,
    handoff: bool = False,
) -> IntentClassificationResult:
    return IntentClassificationResult(
        intent=intent,
        confidence=confidence,
        entities=IntentEntities(),
        risk_level=RiskLevel.HIGH if handoff else RiskLevel.LOW,
        recommended_route=(
            IntentRoute.HUMAN_HANDOFF if handoff else IntentRoute.AUTO_REPLY
        ),
        need_human=handoff,
        taxonomy_version="v1",
        routing_reason=(
            RoutingReason.SENSITIVE_INTENT
            if handoff
            else RoutingReason.HIGH_CONFIDENCE_FAQ
        ),
        classification_source=ClassificationSource.MODEL,
    )


def test_perfect_balanced_historical_set_passes_all_g3_intent_gates() -> None:
    cases = _cases()
    predictions = tuple(
        _prediction(case.expected_intent, handoff=case.requires_handoff)
        for case in cases
    )

    report = evaluate_cases(cases, predictions)

    assert report.passed is True
    assert report.sample_count == 200
    assert report.high_confidence_precision == 1.0
    assert report.macro_f1 == 1.0
    assert report.sensitive_handoff_rate == 1.0
    assert len(report.per_class) == len(IntentName)
    assert report.issues == ()


def test_missed_sensitive_handoff_fails_even_when_intents_are_correct() -> None:
    cases = _cases()
    predictions = tuple(
        _prediction(case.expected_intent, handoff=False) for case in cases
    )

    report = evaluate_cases(cases, predictions)

    assert report.macro_f1 == 1.0
    assert report.sensitive_handoff_rate == 0.0
    assert report.passed is False
    assert "sensitive_handoff_below_target" in report.issues


def test_no_high_confidence_predictions_cannot_pass_gate() -> None:
    cases = _cases()
    predictions = tuple(
        _prediction(
            case.expected_intent,
            confidence=0.5,
            handoff=case.requires_handoff,
        )
        for case in cases
    )

    report = evaluate_cases(cases, predictions)

    assert report.high_confidence_count == 0
    assert report.passed is False
    assert "no_high_confidence_cases" in report.issues


def test_high_confidence_precision_below_ninety_five_percent_fails_gate() -> None:
    cases = _cases()
    predictions = [
        _prediction(case.expected_intent, handoff=case.requires_handoff)
        for case in cases
    ]
    changed = 0
    for index, case in enumerate(cases):
        if case.requires_handoff or case.expected_intent is IntentName.UNKNOWN:
            continue
        predictions[index] = predictions[index].model_copy(
            update={"intent": IntentName.UNKNOWN}
        )
        changed += 1
        if changed == 20:
            break

    report = evaluate_cases(cases, tuple(predictions))

    assert report.high_confidence_precision == 0.9
    assert "high_confidence_precision_below_target" in report.issues
    assert report.passed is False


def test_unrepresented_taxonomy_and_sensitive_cases_cannot_pass_gate() -> None:
    cases = tuple(
        GoldenCase(
            case_id=f"product-{index}",
            expected_intent=IntentName.PRODUCT_INQUIRY,
            requires_handoff=False,
            classification_input=IntentClassificationInput(user_text="example"),
        )
        for index in range(200)
    )

    report = evaluate_cases(
        cases, tuple(_prediction(IntentName.PRODUCT_INQUIRY) for _ in cases)
    )

    assert "missing_class_support" in report.issues
    assert "no_sensitive_cases" in report.issues
    assert report.passed is False


def test_dataset_rejects_duplicate_ids_and_raw_uncontracted_fields(
    tmp_path: Path,
) -> None:
    valid = _cases(1)[0].model_dump(mode="json")
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        json.dumps(valid) + "\n" + json.dumps(valid) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate case_id"):
        load_cases(dataset)

    valid["raw_email"] = "private@example.com"
    dataset.write_text(json.dumps(valid) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid record on line 1") as error:
        load_cases(dataset)
    assert "private@example.com" not in str(error.value)


def test_dataset_rejects_detectable_personal_data_without_echoing_it(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "cases.jsonl"
    case = _cases(1)[0].model_dump(mode="json")
    for field_name, private_text in (
        ("user_text", "请联系 13800138000"),
        ("asr_text", "邮箱 private@example.com"),
        ("ocr_text", "身份证号 110101199001011234"),
        ("vlm_text", "收货地址：北京市某区某街道 10 号"),
    ):
        candidate = json.loads(json.dumps(case))
        candidate["classification_input"][field_name] = private_text
        dataset.write_text(
            json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="personal data on line 1") as error:
            load_cases(dataset)
        assert private_text not in str(error.value)

    candidate = json.loads(json.dumps(case))
    candidate["case_id"] = "13800138000"
    dataset.write_text(
        json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="personal data on line 1"):
        load_cases(dataset)


@pytest.mark.asyncio
async def test_runner_checks_every_case_before_sending_private_text() -> None:
    safe, private = _cases(2)
    private = private.model_copy(
        update={
            "classification_input": IntentClassificationInput(
                user_text="private@example.com"
            ),
        }
    )
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json=_prediction(safe.expected_intent).model_dump(mode="json")
        )

    target = EvaluationTarget(
        base_url="http://127.0.0.1:8081",
        project_id=uuid4(),
        provider_id=uuid4(),
        model="test-model",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(
            EvaluationRunError, match="detectable personal data"
        ) as error:
            await run_cases((safe, private), target, "private-key", client)
    assert "private@example.com" not in str(error.value)
    assert requests == []


def test_validate_only_reports_hash_without_customer_content(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = tmp_path / "cases.jsonl"
    case = _cases(1)[0].model_dump(mode="json")
    case["classification_input"]["user_text"] = "private but deidentified text"
    dataset.write_text(json.dumps(case) + "\n", encoding="utf-8")

    assert main(["--input", str(dataset)]) == 0

    output = capsys.readouterr()
    assert "private but deidentified text" not in output.out
    assert json.loads(output.out)["validated_cases"] == 1
    assert len(json.loads(output.out)["dataset_sha256"]) == 64


def test_run_rejects_insufficient_coverage_before_any_api_call(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        json.dumps(_cases(1)[0].model_dump(mode="json")) + "\n", encoding="utf-8"
    )
    monkeypatch.setenv("TGO_INTENT_EVAL_INTERNAL_KEY", "private-key")

    def unexpected_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("no HTTP client should be created")

    monkeypatch.setattr(httpx, "AsyncClient", unexpected_client)
    assert (
        main(
            [
                "--input",
                str(dataset),
                "--run",
                "--base-url",
                "http://127.0.0.1:8081",
                "--project-id",
                str(uuid4()),
                "--provider-id",
                str(uuid4()),
                "--model",
                "test-model",
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert "insufficient_samples" in output.err
    assert "private-key" not in output.err


def test_preflight_requires_all_classes_and_sensitive_cases() -> None:
    cases = tuple(
        GoldenCase(
            case_id=f"case-{index}",
            expected_intent=IntentName.PRODUCT_INQUIRY,
            requires_handoff=False,
            classification_input=IntentClassificationInput(user_text="example"),
        )
        for index in range(200)
    )
    with pytest.raises(ValueError, match="missing_class_support"):
        validate_evaluation_coverage(cases)


def _observed(case: GoldenCase) -> ObservedPrediction:
    return ObservedPrediction(
        case_id=case.case_id,
        intent=case.expected_intent,
        confidence=0.97,
        recommended_route=(
            IntentRoute.HUMAN_HANDOFF
            if case.requires_handoff
            else IntentRoute.AUTO_REPLY
        ),
        need_human=case.requires_handoff,
        taxonomy_version="v1",
        classifier_version="intent-v1",
        policy_version="routing-v1",
    )


def test_historical_predictions_are_aligned_by_case_id_without_model_calls(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = _cases()
    dataset = tmp_path / "gold.jsonl"
    observed = tmp_path / "predictions.jsonl"
    dataset.write_text(
        "\n".join(json.dumps(case.model_dump(mode="json")) for case in cases) + "\n",
        encoding="utf-8",
    )
    observed.write_text(
        "\n".join(
            json.dumps(_observed(case).model_dump(mode="json"))
            for case in reversed(cases)
        )
        + "\n",
        encoding="utf-8",
    )

    def unexpected_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("historical evaluation must not create an HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", unexpected_client)
    assert main(["--input", str(dataset), "--predictions", str(observed)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["passed"] is True
    assert report["sample_count"] == 200
    assert report["classifier_version"] == "intent-v1"
    assert report["policy_version"] == "routing-v1"
    assert len(report["prediction_sha256"]) == 64
    assert "desensitized example" not in json.dumps(report)


def test_historical_predictions_reject_missing_and_mismatched_ids(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cases = _cases()
    dataset = tmp_path / "gold.jsonl"
    observed = tmp_path / "predictions.jsonl"
    dataset.write_text(
        "\n".join(json.dumps(case.model_dump(mode="json")) for case in cases) + "\n",
        encoding="utf-8",
    )
    predictions = [_observed(case).model_dump(mode="json") for case in cases]
    predictions[-1]["case_id"] = "private-customer-id"
    observed.write_text(
        "\n".join(json.dumps(item) for item in predictions) + "\n",
        encoding="utf-8",
    )

    assert main(["--input", str(dataset), "--predictions", str(observed)]) == 2
    output = capsys.readouterr()
    assert "prediction case ids do not match" in output.err
    assert "private-customer-id" not in output.err


def test_historical_predictions_reject_version_mixing_and_customer_fields(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed = tmp_path / "predictions.jsonl"
    first = _observed(_cases(1)[0]).model_dump(mode="json")
    second = {**first, "case_id": "case-1", "policy_version": "routing-v2"}
    observed.write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n",
        encoding="utf-8",
    )
    predictions = load_predictions(observed)
    with pytest.raises(ValueError, match="mixed prediction versions"):
        validate_prediction_versions(predictions.predictions)

    first["raw_customer_text"] = "private@example.com"
    observed.write_text(json.dumps(first) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid prediction on line 1") as error:
        load_predictions(observed)
    assert "private@example.com" not in str(error.value)
    assert "private@example.com" not in capsys.readouterr().err

    first.pop("raw_customer_text")
    first["classifier_version"] = "private@example.com"
    observed.write_text(json.dumps(first) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid prediction on line 1") as error:
        load_predictions(observed)
    assert "private@example.com" not in str(error.value)


@pytest.mark.asyncio
async def test_runner_calls_project_scoped_intent_api_without_logging_content() -> None:
    case = _cases(1)[0]
    expected = _prediction(case.expected_intent)
    project_id = uuid4()
    provider_id = uuid4()
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=expected.model_dump(mode="json"))

    target = EvaluationTarget(
        base_url="http://127.0.0.1:8081",
        project_id=project_id,
        provider_id=provider_id,
        model="test-model",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        predictions = await run_cases((case,), target, "private-key", client)

    assert predictions == (expected,)
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/api/v1/analysis/intent"
    assert request.headers["X-Internal-API-Key"] == "private-key"
    assert request.headers["X-Project-Id"] == str(project_id)
    body = json.loads(request.content)
    assert body["provider_id"] == str(provider_id)
    assert body["model"] == "test-model"
    assert (
        body["classification_input"]["user_text"] == case.classification_input.user_text
    )


@pytest.mark.asyncio
async def test_runner_fails_on_redirect_without_ignoring_a_case() -> None:
    target = EvaluationTarget(
        base_url="http://127.0.0.1:8081",
        project_id=uuid4(),
        provider_id=uuid4(),
        model="test-model",
    )
    transport = httpx.MockTransport(
        lambda _: httpx.Response(302, headers={"Location": "https://example.com"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(EvaluationRunError, match="HTTP 302"):
            await run_cases(_cases(1), target, "private-key", client)


def test_remote_http_target_is_rejected_before_sending_internal_key() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        EvaluationTarget(
            base_url="http://example.com:8081",
            project_id=uuid4(),
            provider_id=uuid4(),
            model="test-model",
        )
