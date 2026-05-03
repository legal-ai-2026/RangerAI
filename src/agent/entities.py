from __future__ import annotations

from typing import Literal

from src.agent.calibration import (
    build_soldier_calibration_profile as _build_soldier_calibration_profile,
)
from src.agent.calibration import calibration_profile_summary, team_calibration_summary_for_runs
from src.agent.store import RunStore
from src.contracts import (
    DevelopmentEdgeTrajectory,
    DevelopmentEdge,
    EntityObservation,
    EntityRecommendation,
    EntityRunReference,
    GraphEdge,
    GraphNode,
    GraphSubgraph,
    MissionStateSummary,
    MissionEntityProjection,
    Observation,
    Phase,
    PerformanceMetric,
    RecommendationRecord,
    RunRecord,
    SoldierCalibrationProfile,
    SoldierEntityProjection,
    SoldierObservationDigest,
    SoldierPerformanceReport,
    SoldierRecommendationGuidance,
    SoldierTrainingTrajectory,
    TaskTrajectoryPoint,
    TaskTrajectorySummary,
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


def build_soldier_training_trajectory(
    store: RunStore,
    soldier_id: str,
    limit: int = 100,
) -> SoldierTrainingTrajectory | None:
    runs = store.list_runs_for_soldier(soldier_id, limit=limit)
    if not runs:
        return None
    observations = _entity_observations_for_soldier(runs, soldier_id)
    recommendations = _entity_recommendations_for_soldier(runs, soldier_id)
    calibration_signals = store.list_calibration_signals(
        target_soldier_id=soldier_id,
        limit=limit,
    )

    go_count = sum(1 for item in observations if item.rating == "GO")
    nogo_count = sum(1 for item in observations if item.rating == "NOGO")
    uncertain_count = sum(1 for item in observations if item.rating == "UNCERTAIN")
    total = len(observations)
    go_rate = round(go_count / total, 2) if total else 0.0

    return SoldierTrainingTrajectory(
        soldier_id=soldier_id,
        run_count=len(runs),
        observation_count=total,
        approved_recommendation_count=sum(
            1 for item in recommendations if item.status == "approved"
        ),
        go_rate=go_rate,
        readiness_score=_readiness_score(go_count, nogo_count, uncertain_count),
        task_summaries=_task_trajectory_summaries(observations),
        development_edges=_development_edge_trajectories(recommendations),
        calibration_profile=calibration_profile_summary(calibration_signals),
        recent_points=[
            TaskTrajectoryPoint(
                run_id=item.run_id,
                mission_id=item.mission_id,
                phase=_phase_for_run(runs, item.run_id),
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
        source_refs=[f"postgres://ranger_runs/{record.run_id}" for record in runs],
        update_refs=_update_refs(store, observations, recommendations),
    )


def build_soldier_calibration_profile(
    store: RunStore,
    soldier_id: str,
    limit: int = 100,
) -> SoldierCalibrationProfile | None:
    return _build_soldier_calibration_profile(store, soldier_id, limit=limit)


def get_recommendation_entity(
    store: RunStore,
    recommendation_id: str,
) -> EntityRecommendation | None:
    run_id = store.find_run_id_for_recommendation(recommendation_id)
    if run_id is None:
        return None
    record = store.get(run_id)
    if record is None:
        return None
    for index, recommendation in enumerate(record.recommendations):
        if recommendation.recommendation.recommendation_id == recommendation_id:
            return _entity_recommendation(record, recommendation, index)
    return None


def list_recent_recommendation_entities(
    store: RunStore,
    mission_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[EntityRecommendation]:
    runs = (
        store.list_runs_for_mission(mission_id, limit=max(limit, 100))
        if mission_id
        else store.list_recent_runs(limit=max(limit, 100))
    )
    recommendations = [
        _entity_recommendation(record, recommendation, index)
        for record in runs
        for index, recommendation in enumerate(record.recommendations)
        if status is None or recommendation.status == status
    ]
    return sorted(
        recommendations,
        key=lambda item: item.recommendation.created_at_utc,
        reverse=True,
    )[:limit]


def build_mission_state_summary(
    store: RunStore,
    mission_id: str,
    limit: int = 100,
) -> MissionStateSummary | None:
    runs = store.list_runs_for_mission(mission_id, limit=limit)
    if not runs:
        return None
    ordered_runs = sorted(runs, key=lambda record: record.ingest.timestamp_utc, reverse=True)
    latest = ordered_runs[0]
    observations = _entity_observations_for_mission(ordered_runs)
    recommendations = _entity_recommendations_for_mission(ordered_runs)
    soldier_ids = sorted(
        {item.soldier_id for item in observations}
        | {item.recommendation.target_soldier_id for item in recommendations}
    )
    readiness_scores = [_soldier_readiness(observations, soldier_id) for soldier_id in soldier_ids]
    platoon_readiness_score = (
        round(sum(readiness_scores) / len(readiness_scores), 1) if readiness_scores else 0.0
    )
    latest_observations = sorted(
        observations,
        key=lambda item: item.timestamp_utc,
        reverse=True,
    )[:10]
    latest_recommendations = sorted(
        recommendations,
        key=lambda item: item.recommendation.created_at_utc,
        reverse=True,
    )[:10]
    return MissionStateSummary(
        mission_id=mission_id,
        latest_run_id=latest.run_id,
        platoon_id=latest.ingest.platoon_id,
        phase=latest.ingest.phase,
        mission_type=latest.ingest.mission_type,
        status=latest.status,
        run_count=len(ordered_runs),
        soldier_ids=soldier_ids,
        total_observations=len(observations),
        pending_recommendations=sum(1 for item in recommendations if item.status == "pending"),
        approved_recommendations=sum(1 for item in recommendations if item.status == "approved"),
        rejected_recommendations=sum(1 for item in recommendations if item.status == "rejected"),
        blocked_recommendations=sum(1 for item in recommendations if item.status == "blocked"),
        platoon_readiness_score=platoon_readiness_score,
        development_edges=sorted(
            {item.recommendation.development_edge for item in recommendations},
            key=lambda edge: edge.value,
        ),
        team_calibration_profile=team_calibration_summary_for_runs(
            store,
            ordered_runs,
            limit=limit,
        ),
        latest_observation_refs=[item.ref for item in latest_observations],
        latest_recommendation_refs=[item.ref for item in latest_recommendations],
        source_refs=[f"postgres://ranger_runs/{record.run_id}" for record in ordered_runs],
    )


def build_graph_subgraph(
    store: RunStore,
    *,
    run_id: str | None = None,
    mission_id: str | None = None,
    soldier_id: str | None = None,
    limit: int = 100,
) -> GraphSubgraph | None:
    runs = _runs_for_subgraph(
        store, run_id=run_id, mission_id=mission_id, soldier_id=soldier_id, limit=limit
    )
    if not runs:
        return None
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}

    def add_node(node: GraphNode) -> None:
        nodes.setdefault(node.node_id, node)

    def add_edge(edge: GraphEdge) -> None:
        edges.setdefault(edge.edge_id, edge)

    for record in runs:
        mission_node_id = f"mission:{record.ingest.mission_id}"
        platoon_node_id = f"platoon:{record.ingest.platoon_id}"
        run_ref = f"postgres://ranger_runs/{record.run_id}"
        add_node(
            GraphNode(
                node_id=mission_node_id,
                label=record.ingest.mission_id,
                kind="Mission",
                properties={
                    "phase": record.ingest.phase.value,
                    "mission_type": record.ingest.mission_type.value,
                    "status": record.status.value,
                    "latest_run_id": record.run_id,
                },
                ref=run_ref,
            )
        )
        add_node(
            GraphNode(
                node_id=platoon_node_id,
                label=record.ingest.platoon_id,
                kind="Platoon",
                properties={"phase": record.ingest.phase.value},
                ref=run_ref,
            )
        )
        add_edge(
            GraphEdge(
                edge_id=f"{platoon_node_id}->PART_OF->{mission_node_id}",
                source_id=platoon_node_id,
                target_id=mission_node_id,
                label="PART_OF",
            )
        )

        for index, observation in enumerate(record.observations):
            observation_node_id = f"observation:{observation.observation_id}"
            soldier_node_id = f"soldier:{observation.soldier_id}"
            task_node_id = f"task:{observation.task_code}"
            add_node(
                GraphNode(
                    node_id=soldier_node_id,
                    label=observation.soldier_id,
                    kind="Soldier",
                    ref=run_ref,
                )
            )
            add_node(
                GraphNode(
                    node_id=task_node_id,
                    label=observation.task_code,
                    kind="TaskStandard",
                    ref=f"pgvector://doctrine/{observation.task_code}",
                )
            )
            add_node(
                GraphNode(
                    node_id=observation_node_id,
                    label=observation.task_code,
                    kind="Observation",
                    properties={
                        "rating": observation.rating,
                        "source": observation.source,
                        "timestamp_utc": observation.timestamp_utc.isoformat(),
                        "note_preview": observation.note[:180],
                    },
                    ref=f"{run_ref}#record.observations[{index}]",
                )
            )
            add_edge(
                GraphEdge(
                    edge_id=f"{soldier_node_id}->MEMBER_OF->{platoon_node_id}",
                    source_id=soldier_node_id,
                    target_id=platoon_node_id,
                    label="MEMBER_OF",
                )
            )
            add_edge(
                GraphEdge(
                    edge_id=f"{soldier_node_id}->HAS_OBSERVATION->{observation_node_id}",
                    source_id=soldier_node_id,
                    target_id=observation_node_id,
                    label="HAS_OBSERVATION",
                    properties={"timestamp_utc": observation.timestamp_utc.isoformat()},
                )
            )
            add_edge(
                GraphEdge(
                    edge_id=f"{observation_node_id}->ON_TASK->{task_node_id}",
                    source_id=observation_node_id,
                    target_id=task_node_id,
                    label="ON_TASK",
                )
            )
            add_edge(
                GraphEdge(
                    edge_id=f"{observation_node_id}->OBSERVED_DURING->{mission_node_id}",
                    source_id=observation_node_id,
                    target_id=mission_node_id,
                    label="OBSERVED_DURING",
                )
            )

        for index, record_item in enumerate(record.recommendations):
            recommendation = record_item.recommendation
            recommendation_node_id = f"recommendation:{recommendation.recommendation_id}"
            soldier_node_id = f"soldier:{recommendation.target_soldier_id}"
            add_node(
                GraphNode(
                    node_id=soldier_node_id,
                    label=recommendation.target_soldier_id,
                    kind="Soldier",
                    ref=run_ref,
                )
            )
            add_node(
                GraphNode(
                    node_id=recommendation_node_id,
                    label=recommendation.development_edge.value,
                    kind="Recommendation",
                    properties={
                        "status": record_item.status,
                        "risk_level": recommendation.risk_level.value,
                        "fairness_score": recommendation.fairness_score,
                        "score": recommendation.score_breakdown.total
                        if recommendation.score_breakdown
                        else None,
                        "decision_quality": recommendation.decision_quality.overall
                        if recommendation.decision_quality
                        else None,
                        "decision_quality_rating": recommendation.decision_quality.rating
                        if recommendation.decision_quality
                        else None,
                    },
                    ref=f"{run_ref}#record.recommendations[{index}]",
                )
            )
            add_edge(
                GraphEdge(
                    edge_id=f"{recommendation_node_id}->TARGETS->{soldier_node_id}",
                    source_id=recommendation_node_id,
                    target_id=soldier_node_id,
                    label="TARGETS",
                )
            )
            for evidence_ref in recommendation.evidence_refs:
                observation_id = _entity_id_from_locator(evidence_ref.ref, "Observation")
                if observation_id:
                    target_id = f"observation:{observation_id}"
                    add_edge(
                        GraphEdge(
                            edge_id=f"{recommendation_node_id}->DERIVED_FROM->{target_id}",
                            source_id=recommendation_node_id,
                            target_id=target_id,
                            label="DERIVED_FROM",
                            properties={"role": evidence_ref.role},
                        )
                    )
            if recommendation.target_ids.task_code:
                task_node_id = f"task:{recommendation.target_ids.task_code}"
                add_node(
                    GraphNode(
                        node_id=task_node_id,
                        label=recommendation.target_ids.task_code,
                        kind="TaskStandard",
                        ref=f"pgvector://doctrine/{recommendation.target_ids.task_code}",
                    )
                )
                add_edge(
                    GraphEdge(
                        edge_id=f"{recommendation_node_id}->CITES->{task_node_id}",
                        source_id=recommendation_node_id,
                        target_id=task_node_id,
                        label="CITES",
                    )
                )

    scope = {
        key: value
        for key, value in {
            "run_id": run_id,
            "mission_id": mission_id,
            "soldier_id": soldier_id,
        }.items()
        if value
    }
    return GraphSubgraph(
        scope=scope,
        nodes=sorted(nodes.values(), key=lambda node: (node.kind, node.node_id)),
        edges=sorted(edges.values(), key=lambda edge: edge.edge_id),
        source_refs=[f"postgres://ranger_runs/{record.run_id}" for record in runs],
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
    observation_ids = {observation.observation_id for observation in observations}
    recommendation_ids = {
        recommendation.recommendation.recommendation_id for recommendation in recommendations
    }
    limit = max(1000, (len(observation_ids) + len(recommendation_ids)) * 10)
    events = store.list_update_events(limit=limit)
    refs = {
        f"postgres://ranger_update_ledger/{event.version_id}"
        for event in events
        if (event.entity_type == "observation" and event.entity_id in observation_ids)
        or (event.entity_type == "recommendation" and event.entity_id in recommendation_ids)
    }
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
        model_context_refs=list(recommendation.model_context_refs),
        policy_refs=list(recommendation.policy_refs),
        evidence_summary=recommendation.evidence_summary,
        why_now=recommendation.why_now,
        expected_learning_signal=recommendation.expected_learning_signal,
        risk_controls=recommendation.risk_controls,
        uncertainty_refs=list(recommendation.uncertainty_refs),
        score_breakdown=recommendation.score_breakdown,
        decision_frame=recommendation.decision_frame,
        decision_quality=recommendation.decision_quality,
        value_of_information=recommendation.value_of_information,
        review_requirements=list(recommendation.review_requirements),
        calibration_support=recommendation.calibration_support,
    )


def _readiness_score(go_count: int, nogo_count: int, uncertain_count: int) -> float:
    raw = 70 + (go_count * 10) - (nogo_count * 15) - (uncertain_count * 5)
    return float(max(0, min(100, raw)))


def _soldier_readiness(observations: list[EntityObservation], soldier_id: str) -> float:
    soldier_observations = [item for item in observations if item.soldier_id == soldier_id]
    return _readiness_score(
        go_count=sum(1 for item in soldier_observations if item.rating == "GO"),
        nogo_count=sum(1 for item in soldier_observations if item.rating == "NOGO"),
        uncertain_count=sum(1 for item in soldier_observations if item.rating == "UNCERTAIN"),
    )


def _runs_for_subgraph(
    store: RunStore,
    *,
    run_id: str | None,
    mission_id: str | None,
    soldier_id: str | None,
    limit: int,
) -> list[RunRecord]:
    if run_id:
        record = store.get(run_id)
        return [] if record is None else [record]
    if mission_id:
        return store.list_runs_for_mission(mission_id, limit=limit)
    if soldier_id:
        return store.list_runs_for_soldier(soldier_id, limit=limit)
    return store.list_recent_runs(limit=limit)


def _entity_id_from_locator(locator: str, entity_type: str) -> str | None:
    marker = f"/{entity_type}/"
    if marker not in locator:
        return None
    return locator.split(marker, maxsplit=1)[1].split("#", maxsplit=1)[0] or None


def _metric_status(value: float) -> Literal["strong", "watch", "critical"]:
    if value >= 75:
        return "strong"
    if value >= 50:
        return "watch"
    return "critical"


def _task_trajectory_summaries(
    observations: list[EntityObservation],
) -> list[TaskTrajectorySummary]:
    by_task: dict[str, list[EntityObservation]] = {}
    for observation in observations:
        by_task.setdefault(observation.task_code, []).append(observation)

    summaries: list[TaskTrajectorySummary] = []
    for task_code, task_observations in by_task.items():
        ordered = sorted(task_observations, key=lambda item: item.timestamp_utc)
        latest = ordered[-1]
        summaries.append(
            TaskTrajectorySummary(
                task_code=task_code,
                go_count=sum(1 for item in ordered if item.rating == "GO"),
                nogo_count=sum(1 for item in ordered if item.rating == "NOGO"),
                uncertain_count=sum(1 for item in ordered if item.rating == "UNCERTAIN"),
                latest_rating=latest.rating,
                latest_timestamp_utc=latest.timestamp_utc,
                trend=_task_trend(ordered),
                source_refs=[item.ref for item in ordered],
            )
        )
    return sorted(summaries, key=lambda item: item.task_code)


def _development_edge_trajectories(
    recommendations: list[EntityRecommendation],
) -> list[DevelopmentEdgeTrajectory]:
    by_edge: dict[DevelopmentEdge, list[EntityRecommendation]] = {}
    for recommendation in recommendations:
        by_edge.setdefault(recommendation.recommendation.development_edge, []).append(
            recommendation
        )

    trajectories: list[DevelopmentEdgeTrajectory] = []
    for edge, edge_recommendations in by_edge.items():
        ordered = sorted(
            edge_recommendations,
            key=lambda item: item.recommendation.created_at_utc,
        )
        latest = ordered[-1]
        trajectories.append(
            DevelopmentEdgeTrajectory(
                development_edge=edge,
                approved_count=sum(1 for item in ordered if item.status == "approved"),
                pending_count=sum(1 for item in ordered if item.status == "pending"),
                rejected_count=sum(1 for item in ordered if item.status == "rejected"),
                blocked_count=sum(1 for item in ordered if item.status == "blocked"),
                latest_recommendation_id=latest.recommendation.recommendation_id,
                source_refs=[item.ref for item in ordered],
            )
        )
    return sorted(trajectories, key=lambda item: item.development_edge.value)


def _task_trend(
    observations: list[EntityObservation],
) -> Literal["improving", "declining", "stable", "insufficient_data"]:
    scored = [item for item in observations if item.rating in {"GO", "NOGO"}]
    if len(scored) < 2:
        return "insufficient_data"
    first = scored[0].rating
    latest = scored[-1].rating
    if first == "NOGO" and latest == "GO":
        return "improving"
    if first == "GO" and latest == "NOGO":
        return "declining"
    return "stable"


def _phase_for_run(runs: list[RunRecord], run_id: str) -> Phase:
    for record in runs:
        if record.run_id == run_id:
            return record.ingest.phase
    raise KeyError(f"run {run_id} not found")
