from __future__ import annotations

from collections import defaultdict
from typing import Literal

from src.contracts import (
    DashboardRunSummary,
    Observation,
    PerformanceMetric,
    RecommendationRecord,
    RunRecord,
    SoldierPerformanceSummary,
)


def build_dashboard_summary(record: RunRecord) -> DashboardRunSummary:
    observations_by_soldier: dict[str, list[Observation]] = defaultdict(list)
    recommendations_by_soldier: dict[str, list[RecommendationRecord]] = defaultdict(list)
    for observation in record.observations:
        observations_by_soldier[observation.soldier_id].append(observation)
    for recommendation in record.recommendations:
        target = recommendation.recommendation.target_soldier_id
        recommendations_by_soldier[target].append(recommendation)

    soldier_ids = sorted(set(observations_by_soldier) | set(recommendations_by_soldier))
    soldiers = [
        _soldier_summary(
            soldier_id,
            observations_by_soldier.get(soldier_id, []),
            recommendations_by_soldier.get(soldier_id, []),
        )
        for soldier_id in soldier_ids
    ]
    platoon_score = (
        round(sum(soldier.readiness_score for soldier in soldiers) / len(soldiers), 1)
        if soldiers
        else 0.0
    )
    return DashboardRunSummary(
        run_id=record.run_id,
        mission_id=record.ingest.mission_id,
        platoon_id=record.ingest.platoon_id,
        phase=record.ingest.phase,
        status=record.status,
        total_observations=len(record.observations),
        pending_recommendations=sum(1 for item in record.recommendations if item.status == "pending"),
        blocked_recommendations=sum(1 for item in record.recommendations if item.status == "blocked"),
        approved_recommendations=sum(1 for item in record.recommendations if item.status == "approved"),
        platoon_readiness_score=platoon_score,
        soldiers=soldiers,
    )


def _soldier_summary(
    soldier_id: str,
    observations: list[Observation],
    recommendations: list[RecommendationRecord],
) -> SoldierPerformanceSummary:
    go_count = sum(1 for item in observations if item.rating == "GO")
    nogo_count = sum(1 for item in observations if item.rating == "NOGO")
    uncertain_count = sum(1 for item in observations if item.rating == "UNCERTAIN")
    total = len(observations)
    go_rate = round(go_count / total, 2) if total else 0.0
    readiness_score = _readiness_score(go_count, nogo_count, uncertain_count)
    active = [
        item.recommendation
        for item in recommendations
        if item.status in {"pending", "approved"}
    ]
    edges = sorted(
        {item.recommendation.development_edge for item in recommendations},
        key=lambda edge: edge.value,
    )
    return SoldierPerformanceSummary(
        soldier_id=soldier_id,
        observations_count=total,
        go_count=go_count,
        nogo_count=nogo_count,
        uncertain_count=uncertain_count,
        go_rate=go_rate,
        readiness_score=readiness_score,
        metrics=[
            PerformanceMetric(
                name="Task performance",
                value=round(go_rate * 100, 1),
                max_value=100,
                status=_metric_status(go_rate * 100),
            ),
            PerformanceMetric(
                name="Development pressure",
                value=float(len(active)),
                max_value=3,
                status="critical" if len(active) > 2 else "watch" if active else "strong",
            ),
            PerformanceMetric(
                name="Evaluation confidence",
                value=round(((total - uncertain_count) / total) * 100, 1) if total else 0.0,
                max_value=100,
                status=_metric_status(((total - uncertain_count) / total) * 100 if total else 0),
            ),
        ],
        development_edges=edges,
        active_recommendations=active,
    )


def _readiness_score(go_count: int, nogo_count: int, uncertain_count: int) -> float:
    raw = 70 + (go_count * 10) - (nogo_count * 15) - (uncertain_count * 5)
    return float(max(0, min(100, raw)))


def _metric_status(value: float) -> Literal["strong", "watch", "critical"]:
    if value >= 75:
        return "strong"
    if value >= 50:
        return "watch"
    return "critical"
