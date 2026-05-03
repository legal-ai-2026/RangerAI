from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.contracts import (
    DevelopmentEdge,
    EvidenceRef,
    GeoPoint,
    IngestEnvelope,
    Phase,
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
        IngestEnvelope(
            instructor_id="ri-1",
            platoon_id="plt-1",
            mission_id="m-1",
            phase=Phase.mountain,
            timestamp_utc=datetime.now(timezone.utc),
            geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
            free_text="Jones blew Phase Line Bird",
            unexpected=True,
        )


def test_recommendation_decision_rejects_unknown_action() -> None:
    with pytest.raises(ValidationError):
        RecommendationDecision(decision="defer")


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
