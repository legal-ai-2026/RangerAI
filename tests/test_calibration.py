from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.agent.calibration import (
    CALIBRATION_HISTORY_REVIEW,
    attach_calibration_support,
    build_soldier_calibration_profile,
    build_team_calibration_profile,
)
from src.agent.store import InMemoryRunStore
from src.contracts import (
    CalibrationOutcome,
    CalibrationCueTag,
    CalibrationSignal,
    DevelopmentEdge,
    GeoPoint,
    IngestEnvelope,
    Phase,
    PolicyDecision,
    RecommendationRecord,
    RiskLevel,
    RunRecord,
    RunStatus,
    ScenarioRecommendation,
    TargetIds,
)


def test_calibration_signal_rejects_unknown_cue_tag() -> None:
    with pytest.raises(ValidationError):
        CalibrationSignal.model_validate(
            {
                "recommendation_id": "rec-1",
                "run_id": "run-1",
                "instructor_id": "ri-1",
                "outcome": "improved",
                "cue_tags": ["unknown_cue"],
                "observed_learning_signal": "Jones issued the expected SITREP.",
            }
        )


def test_calibration_profile_aggregates_feedback_by_cue_and_intervention() -> None:
    store = InMemoryRunStore()
    store.put(_run())
    assert store.put_calibration_signal(_signal("cal-1", "improved")) is True
    assert store.put_calibration_signal(_signal("cal-1", "improved")) is False
    store.put_calibration_signal(_signal("cal-2", "no_change"))

    profile = build_soldier_calibration_profile(store, "Jones")

    assert profile is not None
    assert profile.signal_count == 2
    assert profile.outcome_counts == {"improved": 1, "no_change": 1}
    assert profile.outcome_trend == "mixed"
    assert profile.cue_profiles[0].cue_tag == CalibrationCueTag.communication_timing
    assert profile.intervention_profiles[0].intervention_id == "comm_degraded_sitrep"


def test_negative_calibration_history_requires_review_acknowledgement() -> None:
    store = InMemoryRunStore()
    store.put_calibration_signal(_signal("cal-1", "worsened"))
    store.put_calibration_signal(_signal("cal-2", "unsafe_abort"))
    record = RecommendationRecord(
        recommendation=_recommendation(),
        policy=PolicyDecision(allowed=True, reasons=[], fairness_score=1.0),
        status="pending",
    )

    enriched = attach_calibration_support([record], store)[0].recommendation

    assert enriched.calibration_support is not None
    assert enriched.calibration_support.outcome_trend == "negative"
    assert any(
        requirement.requirement_id == CALIBRATION_HISTORY_REVIEW
        for requirement in enriched.review_requirements
    )
    assert enriched.value_of_information is not None
    assert enriched.value_of_information.collect_more is True


def test_team_calibration_profile_aggregates_mission_feedback_only() -> None:
    store = InMemoryRunStore()
    store.put(_run(run_id="run-1", mission_id="m-1", soldier_id="Jones", rec_id="rec-1"))
    store.put(_run(run_id="run-2", mission_id="m-1", soldier_id="Smith", rec_id="rec-2"))
    store.put(_run(run_id="run-3", mission_id="m-2", soldier_id="Brown", rec_id="rec-3"))
    store.put_calibration_signal(
        _signal(
            "cal-1",
            "improved",
            run_id="run-1",
            recommendation_id="rec-1",
            soldier_id="Jones",
            development_edge=DevelopmentEdge.communications,
            cue_tag=CalibrationCueTag.communication_timing,
        )
    )
    store.put_calibration_signal(
        _signal(
            "cal-2",
            "worsened",
            run_id="run-2",
            recommendation_id="rec-2",
            soldier_id="Smith",
            development_edge=DevelopmentEdge.team_accountability,
            cue_tag=CalibrationCueTag.team_coordination,
        )
    )
    store.put_calibration_signal(
        _signal(
            "cal-3",
            "improved",
            run_id="run-3",
            recommendation_id="rec-3",
            soldier_id="Brown",
        )
    )

    profile = build_team_calibration_profile(store, "m-1")

    assert profile is not None
    assert profile.mission_id == "m-1"
    assert profile.platoon_id == "plt-1"
    assert profile.run_count == 2
    assert profile.soldier_count == 2
    assert profile.signal_count == 2
    assert profile.outcome_counts == {"improved": 1, "worsened": 1}
    assert profile.outcome_trend == "negative"
    assert {item.soldier_id for item in profile.member_summaries} == {"Jones", "Smith"}
    assert {item.development_edge for item in profile.development_edge_profiles} == {
        DevelopmentEdge.communications,
        DevelopmentEdge.team_accountability,
    }
    assert all("cal-3" not in ref for ref in profile.source_refs)


def test_team_calibration_profile_returns_insufficient_data_when_mission_has_no_feedback() -> None:
    store = InMemoryRunStore()
    store.put(_run())

    profile = build_team_calibration_profile(store, "m-1")

    assert profile is not None
    assert profile.signal_count == 0
    assert profile.outcome_counts == {}
    assert profile.outcome_trend == "insufficient_data"
    assert profile.cue_profiles == []
    assert profile.member_summaries == []
    assert profile.source_refs == ["postgres://ranger_runs/run-1"]


def _signal(
    signal_id: str,
    outcome: CalibrationOutcome,
    *,
    run_id: str = "run-1",
    recommendation_id: str = "rec-1",
    soldier_id: str = "Jones",
    development_edge: DevelopmentEdge = DevelopmentEdge.communications,
    cue_tag: CalibrationCueTag = CalibrationCueTag.communication_timing,
) -> CalibrationSignal:
    return CalibrationSignal(
        signal_id=signal_id,
        recommendation_id=recommendation_id,
        run_id=run_id,
        instructor_id="ri-1",
        target_soldier_id=soldier_id,
        task_code="MV-2",
        development_edge=development_edge,
        intervention_id="comm_degraded_sitrep",
        outcome=outcome,
        cue_tags=[cue_tag],
        observed_learning_signal=f"{soldier_id} issued the expected SITREP.",
        occurred_at_utc=datetime.now(timezone.utc),
    )


def _recommendation(
    *,
    recommendation_id: str = "rec-new",
    soldier_id: str = "Jones",
    development_edge: DevelopmentEdge = DevelopmentEdge.communications,
) -> ScenarioRecommendation:
    return ScenarioRecommendation(
        recommendation_id=recommendation_id,
        target_soldier_id=soldier_id,
        rationale="Observed communication friction supports a supervised reporting inject.",
        development_edge=development_edge,
        proposed_modification=f"Have {soldier_id} issue a supervised SITREP at the next halt.",
        doctrine_refs=["TC 3-21.76 MV-2"],
        estimated_duration_min=10,
        risk_level=RiskLevel.low,
        fairness_score=1.0,
        target_ids=TargetIds(soldier_id=soldier_id, task_code="MV-2"),
        intervention_id="comm_degraded_sitrep",
    )


def _run(
    *,
    run_id: str = "run-1",
    mission_id: str = "m-1",
    platoon_id: str = "plt-1",
    soldier_id: str = "Jones",
    rec_id: str = "rec-1",
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        status=RunStatus.completed,
        ingest=IngestEnvelope(
            instructor_id="ri-1",
            platoon_id=platoon_id,
            mission_id=mission_id,
            phase=Phase.mountain,
            geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
            free_text=f"{soldier_id} blew Phase Line Bird.",
        ),
        recommendations=[
            RecommendationRecord(
                recommendation=_recommendation(
                    recommendation_id=rec_id,
                    soldier_id=soldier_id,
                ),
                policy=PolicyDecision(allowed=True, reasons=[], fairness_score=1.0),
                status="approved",
            )
        ],
    )
