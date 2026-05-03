from src.contracts import (
    DevelopmentEdge,
    EvidenceRef,
    RiskLevel,
    ScenarioRecommendation,
    TargetIds,
)
from src.kg.client import KGClient


class FakeGraph:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, object]]] = []

    def query(self, query: str, params: dict[str, object] | None = None) -> None:
        self.queries.append((query, params or {}))


def test_write_recommendation_adds_provenance_edges() -> None:
    graph = FakeGraph()
    client = KGClient()
    client._graph = graph
    recommendation = ScenarioRecommendation(
        recommendation_id="rec-1",
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

    client.write_recommendation(recommendation)

    assert any("DERIVED_FROM" in query for query, _params in graph.queries)
    assert any("CITES" in query for query, _params in graph.queries)
    assert {"recommendation_id": "rec-1", "observation_id": "obs-1"} in [
        params for _query, params in graph.queries
    ]
    assert {"recommendation_id": "rec-1", "task_code": "MV-2"} in [
        params for _query, params in graph.queries
    ]
