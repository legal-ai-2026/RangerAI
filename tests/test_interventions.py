from src.agent.interventions import draft_intervention_recommendations
from src.contracts import DevelopmentEdge, Observation, RiskLevel
from src.ingest.providers import heuristic_recommendations


def test_intervention_library_scores_and_ranks_observed_development_edges() -> None:
    recommendations = heuristic_recommendations(
        [
            Observation(
                soldier_id="Garcia",
                task_code="AM-4",
                note="Garcia ran a textbook ambush rehearsal.",
                rating="GO",
                source="free_text",
            ),
            Observation(
                soldier_id="Jones",
                task_code="MV-2",
                note="Jones blew Phase Line Bird and gave no SITREP.",
                rating="NOGO",
                source="free_text",
            ),
            Observation(
                soldier_id="Smith",
                task_code="PB-7",
                note="Smith asleep at 0300 during patrol-base security.",
                rating="NOGO",
                source="free_text",
            ),
        ]
    )

    assert [item.target_soldier_id for item in recommendations] == ["Jones", "Smith", "Garcia"]
    assert recommendations[0].intervention_id == "comm_degraded_sitrep"
    assert recommendations[0].development_edge == DevelopmentEdge.communications
    assert recommendations[0].learning_objective
    first_score = recommendations[0].score_breakdown
    last_score = recommendations[2].score_breakdown
    assert first_score is not None
    assert last_score is not None
    assert first_score.learning_delta > last_score.learning_delta
    assert first_score.total > last_score.total


def test_intervention_score_flags_fatigue_without_unsafe_overload() -> None:
    recommendation = draft_intervention_recommendations(
        [
            Observation(
                soldier_id="Smith",
                task_code="PB-7",
                note="Smith asleep at 0300 during patrol-base security.",
                rating="NOGO",
                source="free_text",
            )
        ]
    )[0]

    assert recommendation.risk_level == RiskLevel.medium
    assert recommendation.score_breakdown is not None
    assert recommendation.score_breakdown.fatigue_overload > 0.2
    assert all(
        "immersion" in item.lower() or "fatigue" in item.lower()
        for item in recommendation.safety_checks
    )


def test_intervention_library_does_not_target_unknown_soldier() -> None:
    assert (
        draft_intervention_recommendations(
            [
                Observation(
                    soldier_id="UNKNOWN",
                    task_code="UNMAPPED",
                    note="Unclear OCR row.",
                    rating="UNCERTAIN",
                    source="image",
                )
            ]
        )
        == []
    )
