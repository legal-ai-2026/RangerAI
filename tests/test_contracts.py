from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.contracts import (
    DecisionQuality,
    DevelopmentEdge,
    EvidenceRef,
    GeoPoint,
    IngestEnvelope,
    LessonsLearnedSignal,
    Phase,
    RecommendationScore,
    RecommendationDecision,
    RiskLevel,
    ScenarioRecommendation,
    TargetIds,
)


def test_ingest_rejects_missing_payload() -> None:
    with pytest.raises(ValidationError):
        IngestEnvelope(
            instructor_id="ri-1",
            platoon_id="plt-1",
            mission_id="m-1",
            phase=Phase.mountain,
            timestamp_utc=datetime.now(timezone.utc),
            geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
        )


def test_ingest_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        IngestEnvelope.model_validate(
            {
                "instructor_id": "ri-1",
                "platoon_id": "plt-1",
                "mission_id": "m-1",
                "phase": Phase.mountain,
                "timestamp_utc": datetime.now(timezone.utc),
                "geo": GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
                "free_text": "Jones blew Phase Line Bird",
                "unexpected": True,
            }
        )


def test_recommendation_decision_rejects_unknown_action() -> None:
    with pytest.raises(ValidationError):
        RecommendationDecision.model_validate({"decision": "defer"})


def test_recommendation_decision_rejects_edit_without_approval() -> None:
    edited = ScenarioRecommendation(
        target_soldier_id="Jones",
        rationale="Instructor edit keeps the recommendation bounded and evidence-linked.",
        development_edge=DevelopmentEdge.communications,
        proposed_modification="Have Jones issue a two-minute SITREP at the next halt.",
        doctrine_refs=["TC 3-21.76 MV-2"],
        estimated_duration_min=10,
        risk_level=RiskLevel.low,
        fairness_score=1.0,
    )

    with pytest.raises(ValidationError):
        RecommendationDecision(decision="reject", edited_recommendation=edited)


def test_lesson_signal_requires_canonical_linkage() -> None:
    with pytest.raises(ValidationError):
        LessonsLearnedSignal(
            lesson_id="lesson-1",
            summary="A valid summary still needs a canonical mission, task, soldier, or recommendation link.",
        )


def test_recommendation_carries_cross_system_provenance() -> None:
    recommendation = ScenarioRecommendation(
        target_soldier_id="Jones",
        rationale="Observed task friction supports a focused supervised development event.",
        development_edge=DevelopmentEdge.communications,
        proposed_modification="Run a supervised five-point SITREP drill at the next halt.",
        doctrine_refs=["TC 3-21.76 MV-2"],
        safety_checks=["No immersion added."],
        estimated_duration_min=10,
        risk_level=RiskLevel.low,
        fairness_score=1.0,
        target_ids=TargetIds(
            soldier_id="Jones",
            platoon_id="plt-1",
            mission_id="m-1",
            task_code="MV-2",
        ),
        evidence_refs=[
            EvidenceRef(
                ref="falkor://ranger/Observation/obs-1#note",
                role="primary_observation",
            )
        ],
    )

    assert recommendation.target_ids.mission_id == "m-1"
    assert recommendation.evidence_refs[0].role == "primary_observation"
    assert recommendation.created_at_utc.tzinfo is not None


def test_legacy_score_breakdown_defaults_new_uncertainty_fields() -> None:
    score = RecommendationScore.model_validate(
        {
            "learning_delta": 0.8,
            "doctrinal_fit": 0.9,
            "instructor_utility": 0.7,
            "novelty_bonus": 0.1,
            "safety_risk": 0.1,
            "fatigue_overload": 0.1,
            "fairness_penalty": 0.0,
            "repetition_penalty": 0.0,
            "total": 2.3,
        }
    )

    assert score.observability == 0.0
    assert score.uncertainty_penalty == 0.0


def test_decision_quality_forbids_out_of_range_scores() -> None:
    with pytest.raises(ValidationError):
        DecisionQuality(
            information_quality=1.2,
            safety_margin=1.0,
            fairness_margin=1.0,
            observability=0.8,
            learning_utility=0.8,
            reliance_risk=0.1,
            overall=0.8,
            rating="strong",
        )


def test_recommendation_decision_accepts_rationale_and_acknowledgements() -> None:
    decision = RecommendationDecision(
        decision="approve",
        decision_rationale="Instructor reviewed the cited evidence and accepts the risk controls.",
        acknowledged_review_requirements=["medium_risk_ack"],
    )

    assert decision.decision_rationale is not None
    assert decision.acknowledged_review_requirements == ["medium_risk_ack"]
