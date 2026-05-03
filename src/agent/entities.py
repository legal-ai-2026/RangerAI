from __future__ import annotations

from typing import Literal

from src.agent.store import RunStore
from src.contracts import (
    DevelopmentEdge,
    EntityObservation,
    EntityRecommendation,
    EntityRunReference,
    MissionEntityProjection,
    Observation,
    PerformanceMetric,
    RecommendationRecord,
    RunRecord,
    SoldierEntityProjection,
    SoldierObservationDigest,
    SoldierPerformanceReport,
    SoldierRecommendationGuidance,
)


def build_soldier_entity_projection(
    store: RunStore,
    soldier_id: str,
    limit: int = 100,
) -> SoldierEntityProjection | None:
    runs = store.list_runs_for_soldier(soldier_id, limit=limit)
    if not runs:
        return None
    observations = _entity_observations_for_soldier(runs, soldier_id)
    recommendations = _entity_recommendations_for_soldier(runs, soldier_id)
    return SoldierEntityProjection(
        soldier_id=soldier_id,
        runs=_run_refs(runs),
        observations=observations,
        recommendations=recommendations,
        update_refs=_update_refs(store, observations, recommendations),
    )


def build_mission_entity_projection(
    store: RunStore,
    mission_id: str,
    limit: int = 100,
) -> MissionEntityProjection | None:
    runs = store.list_runs_for_mission(mission_id, limit=limit)
    if not runs:
        return None
    observations = _entity_observations_for_mission(runs)
    recommendations = _entity_recommendations_for_mission(runs)
    soldier_ids = sorted(
        {item.soldier_id for item in observations}
        | {item.recommendation.target_soldier_id for item in recommendations}
    )
    return MissionEntityProjection(
        mission_id=mission_id,
        runs=_run_refs(runs),
        soldier_ids=soldier_ids,
        observations=observations,
        recommendations=recommendations,
        update_refs=_update_refs(store, observations, recommendations),
    )


def build_soldier_performance_report(
    store: RunStore,
    soldier_id: str,
    limit: int = 100,
) -> SoldierPerformanceReport | None:
    runs = store.list_runs_for_soldier(soldier_id, limit=limit)
    if not runs:
        return None
    observations = _entity_observations_for_soldier(runs, soldier_id)
    recommendations = _entity_recommendations_for_soldier(runs, soldier_id)
    approved = [item for item in recommendations if item.status == "approved"]

    go_count = sum(1 for item in observations if item.rating == "GO")
    nogo_count = sum(1 for item in observations if item.rating == "NOGO")
    uncertain_count = sum(1 for item in observations if item.rating == "UNCERTAIN")
    total = len(observations)
    go_rate = round(go_count / total, 2) if total else 0.0
    readiness_score = _readiness_score(go_count, nogo_count, uncertain_count)
    approved_count = len(approved)
    confidence = round(((total - uncertain_count) / total) * 100, 1) if total else 0.0

    return SoldierPerformanceReport(
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
                name="Instructor-approved development",
                value=float(approved_count),
                max_value=3,
                status="critical"
                if approved_count > 2
                else "watch"
                if approved_count
                else "strong",
            ),
            PerformanceMetric(
                name="Evaluation confidence",
                value=confidence,
                max_value=100,
                status=_metric_status(confidence),
            ),
        ],
        development_edges=_approved_development_edges(approved),
        approved_recommendations=[
            _soldier_guidance(item)
            for item in sorted(
                approved,
                key=lambda item: item.recommendation.created_at_utc,
                reverse=True,
            )
        ],
        pending_review_count=sum(1 for item in recommendations if item.status == "pending"),
        blocked_recommendation_count=sum(1 for item in recommendations if item.status == "blocked"),
        recent_observations=[
            SoldierObservationDigest(
                run_id=item.run_id,
                mission_id=item.mission_id,
                task_code=item.task_code,
                rating=item.rating,
                timestamp_utc=item.timestamp_utc,
                source_ref=item.ref,
            )
            for item in sorted(
                observations,
                key=lambda item: item.timestamp_utc,
                reverse=True,
            )[:10]
        ],
    )


def _run_refs(runs: list[RunRecord]) -> list[EntityRunReference]:
    return [
        EntityRunReference(
            run_id=record.run_id,
            mission_id=record.ingest.mission_id,
            platoon_id=record.ingest.platoon_id,
            phase=record.ingest.phase,
            status=record.status,
            timestamp_utc=record.ingest.timestamp_utc,
            ref=f"postgres://ranger_runs/{record.run_id}",
        )
        for record in runs
    ]


def _entity_observations_for_soldier(
    runs: list[RunRecord],
    soldier_id: str,
) -> list[EntityObservation]:
    return [
        _entity_observation(record, observation, index)
        for record in runs
        for index, observation in enumerate(record.observations)
        if observation.soldier_id == soldier_id
    ]


def _entity_observations_for_mission(runs: list[RunRecord]) -> list[EntityObservation]:
    return [
        _entity_observation(record, observation, index)
        for record in runs
        for index, observation in enumerate(record.observations)
    ]


def _entity_observation(
    record: RunRecord,
    observation: Observation,
    index: int,
) -> EntityObservation:
    return EntityObservation(
        run_id=record.run_id,
        observation_id=observation.observation_id,
        soldier_id=observation.soldier_id,
        mission_id=record.ingest.mission_id,
        platoon_id=record.ingest.platoon_id,
        task_code=observation.task_code,
        rating=observation.rating,
        note=observation.note,
        source=observation.source,
        timestamp_utc=observation.timestamp_utc,
        ref=f"postgres://ranger_runs/{record.run_id}#record.observations[{index}]",
    )


def _entity_recommendations_for_soldier(
    runs: list[RunRecord],
    soldier_id: str,
) -> list[EntityRecommendation]:
    return [
        _entity_recommendation(record, recommendation, index)
        for record in runs
        for index, recommendation in enumerate(record.recommendations)
        if _recommendation_targets_soldier(recommendation, soldier_id)
    ]


def _entity_recommendations_for_mission(runs: list[RunRecord]) -> list[EntityRecommendation]:
    return [
        _entity_recommendation(record, recommendation, index)
        for record in runs
        for index, recommendation in enumerate(record.recommendations)
    ]


def _entity_recommendation(
    record: RunRecord,
    recommendation: RecommendationRecord,
    index: int,
) -> EntityRecommendation:
    return EntityRecommendation(
        run_id=record.run_id,
        mission_id=record.ingest.mission_id,
        platoon_id=record.ingest.platoon_id,
        recommendation=recommendation.recommendation,
        policy=recommendation.policy,
        status=recommendation.status,
        ref=f"postgres://ranger_runs/{record.run_id}#record.recommendations[{index}]",
    )


def _recommendation_targets_soldier(
    recommendation: RecommendationRecord,
    soldier_id: str,
) -> bool:
    return (
        recommendation.recommendation.target_soldier_id == soldier_id
        or recommendation.recommendation.target_ids.soldier_id == soldier_id
    )


def _update_refs(
    store: RunStore,
    observations: list[EntityObservation],
    recommendations: list[EntityRecommendation],
) -> list[str]:
    refs: set[str] = set()
    for observation in observations:
        refs.update(
            f"postgres://ranger_update_ledger/{event.version_id}"
            for event in store.list_update_events(
                entity_type="observation",
                entity_id=observation.observation_id,
            )
        )
    for recommendation in recommendations:
        refs.update(
            f"postgres://ranger_update_ledger/{event.version_id}"
            for event in store.list_update_events(
                entity_type="recommendation",
                entity_id=recommendation.recommendation.recommendation_id,
            )
        )
    return sorted(refs)


def _approved_development_edges(
    approved: list[EntityRecommendation],
) -> list[DevelopmentEdge]:
    return sorted(
        {item.recommendation.development_edge for item in approved},
        key=lambda edge: edge.value,
    )


def _soldier_guidance(item: EntityRecommendation) -> SoldierRecommendationGuidance:
    recommendation = item.recommendation
    return SoldierRecommendationGuidance(
        recommendation_id=recommendation.recommendation_id,
        run_id=item.run_id,
        mission_id=item.mission_id,
        development_edge=recommendation.development_edge,
        rationale=recommendation.rationale,
        proposed_modification=recommendation.proposed_modification,
        doctrine_refs=list(recommendation.doctrine_refs),
        safety_checks=list(recommendation.safety_checks),
        estimated_duration_min=recommendation.estimated_duration_min,
        requires_resources=list(recommendation.requires_resources),
        risk_level=recommendation.risk_level,
        fairness_score=recommendation.fairness_score,
        evidence_refs=list(recommendation.evidence_refs),
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
