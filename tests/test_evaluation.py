from src.agent.decision_science import add_decision_support_metadata
from src.agent.evaluation import (
    ExpectedEvaluationFixture,
    ExpectedObservation,
    ExpectedRecommendationOpportunity,
    ProviderDiagnostic,
    evaluate_records,
)
from src.contracts import (
    DevelopmentEdge,
    GeoPoint,
    IngestEnvelope,
    Observation,
    Phase,
    PolicyDecision,
    RecommendationRecord,
    RiskLevel,
    RunRecord,
    RunStatus,
    ScenarioRecommendation,
    TargetIds,
)


def test_evaluator_requires_llm_refs_when_requested() -> None:
    report = evaluate_records([_run(metadata=True)], _expected(), require_llm=True)

    assert not report.ok
    assert _metric(report, "llm_application_coverage").passed is False
    assert any(failure.ref == "llm_application_coverage" for failure in report.failures)


def test_evaluator_flags_policy_invariant_violations() -> None:
    recommendation = _recommendation(
        target_soldier_id="Taylor",
        risk_level=RiskLevel.high,
    ).model_copy(update={"doctrine_refs": []})
    record = _run(
        recommendation=recommendation,
        policy=PolicyDecision(allowed=False, reasons=["blocked"], fairness_score=1.0),
        status="approved",
    )

    report = evaluate_records([record], _expected(), min_score=0.0)

    assert not report.ok
    messages = " ".join(failure.message for failure in report.failures)
    assert "absent from extracted roster" in messages
    assert "missing doctrine_refs" in messages
    assert "high-risk recommendation was approved" in messages
    assert "approved despite blocked policy" in messages


def test_evaluator_fails_missing_decision_support_metadata() -> None:
    report = evaluate_records([_run(metadata=False)], _expected())

    assert not report.ok
    assert _metric(report, "decision_support_coverage").passed is False


def test_evaluator_fails_on_provider_fallback_when_required() -> None:
    diagnostic = ProviderDiagnostic(
        stage="recommendation_ranking",
        provider="library",
        status="fallback",
        message="OpenAI recommendation ranking failed.",
    )

    report = evaluate_records(
        [_run(metadata=True)],
        _expected(),
        provider_diagnostics=[diagnostic],
        fail_on_fallback=True,
    )

    assert not report.ok
    assert _metric(report, "provider_fallback_visibility").passed is False
    assert any(failure.category == "provider_fallback" for failure in report.failures)


def test_evaluator_scores_only_processed_envelopes() -> None:
    expected = _expected()
    expected = expected.model_copy(
        update={
            "observations": [
                *expected.observations,
                ExpectedObservation(
                    envelope_id="eval-002",
                    soldier_id="Smith",
                    task_code="PB-7",
                    rating="NOGO",
                ),
            ],
            "recommendation_opportunities": [
                *expected.recommendation_opportunities,
                ExpectedRecommendationOpportunity(
                    envelope_id="eval-002",
                    target_soldier_id="Smith",
                    intervention_id="security_priorities_reset",
                ),
            ],
        }
    )

    report = evaluate_records([_run(metadata=True)], expected)

    assert report.ok
    assert _metric(report, "observation_extraction_match_rate").score == 1.0
    assert _metric(report, "recommendation_opportunity_hit_rate").score == 1.0


def _run(
    *,
    recommendation: ScenarioRecommendation | None = None,
    policy: PolicyDecision | None = None,
    status: str = "approved",
    metadata: bool = False,
) -> RunRecord:
    observation = Observation(
        soldier_id="Jones",
        task_code="MV-2",
        note="Jones blew Phase Line Bird and gave no SITREP.",
        rating="NOGO",
        source="free_text",
    )
    policy = policy or PolicyDecision(allowed=True, reasons=[], fairness_score=1.0)
    recommendation = recommendation or _recommendation()
    if metadata:
        recommendation = add_decision_support_metadata(recommendation, policy, None)
    return RunRecord(
        run_id="run-eval-1",
        status=RunStatus.completed,
        ingest=IngestEnvelope(
            envelope_id="eval-001",
            instructor_id="ri-1",
            platoon_id="plt-1",
            mission_id="m-1",
            phase=Phase.benning,
            geo=GeoPoint(lat=32.36, lon=-84.95, grid_mgrs="16S TEST"),
            free_text="Jones blew Phase Line Bird and gave no SITREP.",
        ),
        observations=[observation],
        recommendations=[
            RecommendationRecord(
                recommendation=recommendation,
                policy=policy,
                status=status,  # type: ignore[arg-type]
            )
        ],
    )


def _recommendation(
    *,
    target_soldier_id: str = "Jones",
    risk_level: RiskLevel = RiskLevel.low,
) -> ScenarioRecommendation:
    return ScenarioRecommendation(
        recommendation_id="rec-eval-1",
        target_soldier_id=target_soldier_id,
        rationale="Observed communication lapse supports a supervised reporting inject.",
        development_edge=DevelopmentEdge.communications,
        proposed_modification="Have Jones issue a supervised SITREP at the next halt.",
        doctrine_refs=["TC 3-21.76 MV-2"],
        estimated_duration_min=12,
        risk_level=risk_level,
        fairness_score=1.0,
        target_ids=TargetIds(soldier_id=target_soldier_id, task_code="MV-2"),
        intervention_id="comm_degraded_sitrep",
    )


def _expected() -> ExpectedEvaluationFixture:
    return ExpectedEvaluationFixture(
        fixture_id="eval",
        observations=[
            ExpectedObservation(
                envelope_id="eval-001",
                soldier_id="Jones",
                task_code="MV-2",
                rating="NOGO",
            )
        ],
        recommendation_opportunities=[
            ExpectedRecommendationOpportunity(
                envelope_id="eval-001",
                target_soldier_id="Jones",
                intervention_id="comm_degraded_sitrep",
            )
        ],
    )


def _metric(report, name: str):
    return next(metric for metric in report.metrics if metric.name == name)
