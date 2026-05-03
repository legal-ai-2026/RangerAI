from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import Field

from src.contracts import RiskLevel, RunRecord, StrictModel


ProviderStage = Literal["stt", "ocr", "extraction", "recommendation_ranking"]
ProviderDiagnosticStatus = Literal["applied", "fallback", "failed"]


class ProviderDiagnostic(StrictModel):
    stage: ProviderStage
    provider: str = Field(min_length=1)
    status: ProviderDiagnosticStatus
    model: str | None = None
    message: str | None = Field(default=None, max_length=700)


class ExpectedObservation(StrictModel):
    envelope_id: str = Field(min_length=1)
    soldier_id: str = Field(min_length=1)
    task_code: str = Field(min_length=1)
    rating: Literal["GO", "NOGO", "UNCERTAIN"]


class ExpectedRecommendationOpportunity(StrictModel):
    envelope_id: str = Field(min_length=1)
    target_soldier_id: str = Field(min_length=1)
    intervention_id: str = Field(min_length=1)


class ExpectedEvaluationFixture(StrictModel):
    fixture_id: str = Field(min_length=1)
    observations: list[ExpectedObservation] = Field(default_factory=list)
    recommendation_opportunities: list[ExpectedRecommendationOpportunity] = Field(
        default_factory=list
    )


class EvaluationMetric(StrictModel):
    name: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)
    threshold: float = Field(ge=0, le=1)
    passed: bool
    details: dict[str, object] = Field(default_factory=dict)


class EvaluationFailure(StrictModel):
    category: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=1000)
    ref: str | None = None
    severity: Literal["error", "warning"] = "error"


class EvaluationReport(StrictModel):
    ok: bool
    fixture_id: str
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_count: int
    overall_score: float = Field(ge=0, le=1)
    min_score: float = Field(ge=0, le=1)
    metrics: list[EvaluationMetric] = Field(default_factory=list)
    failures: list[EvaluationFailure] = Field(default_factory=list)
    provider_diagnostics: list[ProviderDiagnostic] = Field(default_factory=list)


def load_expected_fixture(path: Path) -> ExpectedEvaluationFixture:
    return ExpectedEvaluationFixture.model_validate(json.loads(path.read_text(encoding="utf-8")))


def evaluate_records(
    records: Iterable[RunRecord],
    expected: ExpectedEvaluationFixture,
    *,
    provider_diagnostics: Iterable[ProviderDiagnostic] | None = None,
    min_score: float = 0.85,
    require_llm: bool = False,
    fail_on_fallback: bool = False,
) -> EvaluationReport:
    if not 0 <= min_score <= 1:
        raise ValueError("min_score must be between 0 and 1")
    run_records = list(records)
    expected = _expected_for_records(expected, run_records)
    diagnostics = list(provider_diagnostics or [])
    metrics: list[EvaluationMetric] = [
        _observation_metric(run_records, expected, min_score),
        _recommendation_metric(run_records, expected, min_score),
        _policy_invariant_metric(run_records),
        _decision_support_metric(run_records, min_score),
        _llm_metric(run_records, require_llm=require_llm),
        _provider_fallback_metric(diagnostics, fail_on_fallback=fail_on_fallback),
    ]
    failures = _metric_failures(metrics)
    failures.extend(_policy_failures(run_records))
    failures.extend(_provider_failures(diagnostics, fail_on_fallback=fail_on_fallback))

    overall_score = round(sum(metric.score for metric in metrics) / len(metrics), 3)
    if overall_score < min_score:
        failures.append(
            EvaluationFailure(
                category="overall_score",
                message=(
                    f"overall evaluation score {overall_score:.2f} is below "
                    f"minimum {min_score:.2f}"
                ),
            )
        )

    return EvaluationReport(
        ok=not any(failure.severity == "error" for failure in failures),
        fixture_id=expected.fixture_id,
        run_count=len(run_records),
        overall_score=overall_score,
        min_score=min_score,
        metrics=metrics,
        failures=failures,
        provider_diagnostics=diagnostics,
    )


def _observation_metric(
    records: list[RunRecord],
    expected: ExpectedEvaluationFixture,
    threshold: float,
) -> EvaluationMetric:
    expected_counter = Counter(
        (item.envelope_id, item.soldier_id, item.task_code, item.rating)
        for item in expected.observations
    )
    actual_counter = Counter(
        (
            record.ingest.envelope_id,
            observation.soldier_id,
            observation.task_code,
            observation.rating,
        )
        for record in records
        for observation in record.observations
    )
    matched = _counter_intersection_count(expected_counter, actual_counter)
    expected_count = sum(expected_counter.values())
    score = round(matched / expected_count, 3) if expected_count else 1.0
    missing = list((expected_counter - actual_counter).elements())[:10]
    unexpected = list((actual_counter - expected_counter).elements())[:10]
    return EvaluationMetric(
        name="observation_extraction_match_rate",
        score=score,
        threshold=threshold,
        passed=score >= threshold,
        details={
            "expected": expected_count,
            "actual": sum(actual_counter.values()),
            "matched": matched,
            "missing_sample": [_key_text(item) for item in missing],
            "unexpected_sample": [_key_text(item) for item in unexpected],
        },
    )


def _recommendation_metric(
    records: list[RunRecord],
    expected: ExpectedEvaluationFixture,
    threshold: float,
) -> EvaluationMetric:
    expected_counter = Counter(
        (item.envelope_id, item.target_soldier_id, item.intervention_id)
        for item in expected.recommendation_opportunities
    )
    actual_counter = Counter(
        (
            record.ingest.envelope_id,
            item.recommendation.target_soldier_id,
            item.recommendation.intervention_id or "unknown_intervention",
        )
        for record in records
        for item in record.recommendations
    )
    matched = _counter_intersection_count(expected_counter, actual_counter)
    expected_count = sum(expected_counter.values())
    score = round(matched / expected_count, 3) if expected_count else 1.0
    missing = list((expected_counter - actual_counter).elements())[:10]
    return EvaluationMetric(
        name="recommendation_opportunity_hit_rate",
        score=score,
        threshold=threshold,
        passed=score >= threshold,
        details={
            "expected": expected_count,
            "actual": sum(actual_counter.values()),
            "matched": matched,
            "missing_sample": [_key_text(item) for item in missing],
        },
    )


def _policy_invariant_metric(records: list[RunRecord]) -> EvaluationMetric:
    total = sum(len(record.recommendations) for record in records)
    violation_count = len(_policy_failures(records))
    score = 1.0 if total == 0 else round(max(0.0, 1 - (violation_count / total)), 3)
    return EvaluationMetric(
        name="policy_safety_invariants",
        score=score,
        threshold=1.0,
        passed=violation_count == 0,
        details={"checked_recommendations": total, "violations": violation_count},
    )


def _decision_support_metric(records: list[RunRecord], threshold: float) -> EvaluationMetric:
    checked = 0
    covered = 0
    missing_refs: list[str] = []
    for record in records:
        for item in record.recommendations:
            checked += 1
            recommendation = item.recommendation
            has_required_review = recommendation.risk_level != RiskLevel.medium or bool(
                recommendation.review_requirements
            )
            has_metadata = all(
                (
                    recommendation.decision_frame is not None,
                    recommendation.decision_quality is not None,
                    recommendation.value_of_information is not None,
                    has_required_review,
                )
            )
            if has_metadata:
                covered += 1
            else:
                missing_refs.append(
                    f"{record.ingest.envelope_id}:{recommendation.recommendation_id}"
                )
    score = round(covered / checked, 3) if checked else 1.0
    return EvaluationMetric(
        name="decision_support_coverage",
        score=score,
        threshold=threshold,
        passed=score >= threshold,
        details={
            "checked_recommendations": checked,
            "covered": covered,
            "missing_sample": missing_refs[:10],
        },
    )


def _llm_metric(records: list[RunRecord], *, require_llm: bool) -> EvaluationMetric:
    applied = [
        item.recommendation.recommendation_id
        for record in records
        for item in record.recommendations
        if any(ref.startswith("model://openai/") for ref in item.recommendation.model_context_refs)
    ]
    threshold = 1.0 if require_llm else 0.0
    score = 1.0 if applied or not require_llm else 0.0
    return EvaluationMetric(
        name="llm_application_coverage",
        score=score,
        threshold=threshold,
        passed=score >= threshold,
        details={"applied_recommendations": len(applied), "require_llm": require_llm},
    )


def _provider_fallback_metric(
    diagnostics: list[ProviderDiagnostic],
    *,
    fail_on_fallback: bool,
) -> EvaluationMetric:
    fallback_count = sum(1 for item in diagnostics if item.status in {"fallback", "failed"})
    threshold = 1.0 if fail_on_fallback else 0.0
    score = 0.0 if fail_on_fallback and fallback_count else 1.0
    return EvaluationMetric(
        name="provider_fallback_visibility",
        score=score,
        threshold=threshold,
        passed=score >= threshold,
        details={
            "diagnostic_count": len(diagnostics),
            "fallback_or_failed_count": fallback_count,
            "fail_on_fallback": fail_on_fallback,
        },
    )


def _metric_failures(metrics: list[EvaluationMetric]) -> list[EvaluationFailure]:
    return [
        EvaluationFailure(
            category="metric_threshold",
            message=(
                f"{metric.name} scored {metric.score:.2f}, below threshold "
                f"{metric.threshold:.2f}"
            ),
            ref=metric.name,
        )
        for metric in metrics
        if not metric.passed
    ]


def _policy_failures(records: list[RunRecord]) -> list[EvaluationFailure]:
    failures: list[EvaluationFailure] = []
    for record in records:
        roster = {
            observation.soldier_id
            for observation in record.observations
            if observation.soldier_id not in {"", "UNKNOWN"}
        }
        for item in record.recommendations:
            recommendation = item.recommendation
            ref = f"{record.ingest.envelope_id}:{recommendation.recommendation_id}"
            if recommendation.target_soldier_id not in roster:
                failures.append(
                    EvaluationFailure(
                        category="policy_invariant",
                        message=(
                            "recommendation targets a soldier absent from extracted "
                            f"roster: {recommendation.target_soldier_id}"
                        ),
                        ref=ref,
                    )
                )
            if not recommendation.doctrine_refs:
                failures.append(
                    EvaluationFailure(
                        category="policy_invariant",
                        message="recommendation is missing doctrine_refs",
                        ref=ref,
                    )
                )
            if item.status == "approved" and recommendation.risk_level == RiskLevel.high:
                failures.append(
                    EvaluationFailure(
                        category="policy_invariant",
                        message="high-risk recommendation was approved",
                        ref=ref,
                    )
                )
            if item.status == "approved" and not item.policy.allowed:
                failures.append(
                    EvaluationFailure(
                        category="policy_invariant",
                        message="recommendation was approved despite blocked policy",
                        ref=ref,
                    )
                )
    return failures


def _provider_failures(
    diagnostics: list[ProviderDiagnostic],
    *,
    fail_on_fallback: bool,
) -> list[EvaluationFailure]:
    if not fail_on_fallback:
        return []
    return [
        EvaluationFailure(
            category="provider_fallback",
            message=(
                f"{diagnostic.stage} used {diagnostic.status} provider "
                f"{diagnostic.provider}: {diagnostic.message or 'no detail'}"
            ),
            ref=f"{diagnostic.stage}:{diagnostic.provider}",
        )
        for diagnostic in diagnostics
        if diagnostic.status in {"fallback", "failed"}
    ]


def _expected_for_records(
    expected: ExpectedEvaluationFixture,
    records: list[RunRecord],
) -> ExpectedEvaluationFixture:
    envelope_ids = {record.ingest.envelope_id for record in records}
    if not envelope_ids:
        return expected
    return expected.model_copy(
        update={
            "observations": [
                item for item in expected.observations if item.envelope_id in envelope_ids
            ],
            "recommendation_opportunities": [
                item
                for item in expected.recommendation_opportunities
                if item.envelope_id in envelope_ids
            ],
        }
    )


def _counter_intersection_count(
    expected: Counter[Any],
    actual: Counter[Any],
) -> int:
    return sum((expected & actual).values())


def _key_text(key: tuple[Any, ...]) -> str:
    return ":".join(str(item) for item in key)
