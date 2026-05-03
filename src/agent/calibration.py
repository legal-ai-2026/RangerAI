from __future__ import annotations

from collections import Counter, defaultdict

from src.agent.store import RunStore
from src.contracts import (
    CalibrationCueProfile,
    CalibrationCueTag,
    CalibrationOutcomeTrend,
    CalibrationInterventionProfile,
    CalibrationProfileSummary,
    CalibrationSignal,
    CalibrationSupport,
    DevelopmentEdge,
    EntityRecommendation,
    RecommendationRecord,
    RunRecord,
    ReviewRequirement,
    ScenarioRecommendation,
    SoldierCalibrationProfile,
    TeamCalibrationProfile,
    TeamDevelopmentEdgeCalibrationProfile,
    TeamMemberCalibrationSummary,
    ValueOfInformation,
)

CALIBRATION_HISTORY_REVIEW = "calibration_history_review"


def attach_calibration_support(
    records: list[RecommendationRecord],
    store: RunStore,
) -> list[RecommendationRecord]:
    enriched: list[RecommendationRecord] = []
    for record in records:
        recommendation = record.recommendation
        signals = _signals_for_recommendation_context(store, recommendation)
        support = build_calibration_support(
            recommendation,
            signals,
        )
        enriched.append(
            record.model_copy(
                update={
                    "recommendation": _apply_calibration_metadata(
                        recommendation,
                        support,
                    )
                }
            )
        )
    return enriched


def build_calibration_support(
    recommendation: ScenarioRecommendation,
    signals: list[CalibrationSignal],
) -> CalibrationSupport:
    cue_tags = _cue_tags_to_watch(recommendation, signals)
    task_code = recommendation.target_ids.task_code or "the observed task"
    trend = outcome_trend(signals)
    return CalibrationSupport(
        calibration_goal=(
            f"Calibrate instructor attention on {recommendation.target_soldier_id}'s "
            f"{task_code} cues, not just the written task rule."
        ),
        cue_tags_to_watch=cue_tags,
        feedback_prompt=(
            "After the inject, record whether the expected learning signal appeared, "
            "which cue was easiest to observe, and whether any safety or workload factor "
            "changed the judgement."
        ),
        prior_signal_count=len(signals),
        outcome_trend=trend,
        recommended_feedback_window="same mission phase or next supervised halt",
        source_refs=_signal_refs(signals),
    )


def _apply_calibration_metadata(
    recommendation: ScenarioRecommendation,
    support: CalibrationSupport,
) -> ScenarioRecommendation:
    requirements = list(recommendation.review_requirements)
    value_of_information = recommendation.value_of_information
    if support.outcome_trend == "negative" and not any(
        item.requirement_id == CALIBRATION_HISTORY_REVIEW for item in requirements
    ):
        requirements.append(
            ReviewRequirement(
                requirement_id=CALIBRATION_HISTORY_REVIEW,
                reason=(
                    "Prior calibration feedback for this context is negative; instructor "
                    "must confirm why this inject is still appropriate."
                ),
            )
        )
    if support.outcome_trend in {"mixed", "negative"}:
        value_of_information = ValueOfInformation(
            collect_more=True,
            reason=(
                "Calibration history is not consistently positive, so current instructor "
                "feedback can materially improve future judgement."
            ),
            suggested_action=(
                "Review prior calibration signals and capture post-inject feedback for "
                "the same cue tags."
            ),
        )
    return recommendation.model_copy(
        update={
            "calibration_support": support,
            "review_requirements": requirements,
            "value_of_information": value_of_information,
        }
    )


def build_soldier_calibration_profile(
    store: RunStore,
    soldier_id: str,
    limit: int = 100,
) -> SoldierCalibrationProfile | None:
    runs = store.list_runs_for_soldier(soldier_id, limit=limit)
    signals = store.list_calibration_signals(target_soldier_id=soldier_id, limit=limit)
    if not runs and not signals:
        return None
    return SoldierCalibrationProfile(
        soldier_id=soldier_id,
        signal_count=len(signals),
        outcome_counts=_outcome_counts(signals),
        outcome_trend=outcome_trend(signals),
        cue_profiles=_cue_profiles(signals),
        intervention_profiles=_intervention_profiles(signals),
        source_refs=_signal_refs(signals),
        update_refs=[
            ref
            for signal in signals
            for ref in [
                f"postgres://ranger_calibration_signals/{signal.signal_id}",
                f"postgres://ranger_runs/{signal.run_id}#record.recommendations",
            ]
        ],
    )


def build_team_calibration_profile(
    store: RunStore,
    mission_id: str,
    limit: int = 100,
) -> TeamCalibrationProfile | None:
    runs = store.list_runs_for_mission(mission_id, limit=limit)
    if not runs:
        return None
    ordered_runs = sorted(runs, key=lambda record: record.ingest.timestamp_utc, reverse=True)
    latest = ordered_runs[0]
    signals = _signals_for_runs(store, ordered_runs, limit=limit)
    soldier_ids = _soldier_ids_for_runs(ordered_runs, signals)
    return TeamCalibrationProfile(
        mission_id=mission_id,
        platoon_id=latest.ingest.platoon_id,
        run_count=len(ordered_runs),
        soldier_count=len(soldier_ids),
        signal_count=len(signals),
        outcome_counts=_outcome_counts(signals),
        outcome_trend=outcome_trend(signals),
        cue_profiles=_cue_profiles(signals),
        development_edge_profiles=_development_edge_profiles(signals),
        member_summaries=_member_summaries(signals),
        source_refs=_team_source_refs(ordered_runs, signals),
        update_refs=_team_update_refs(ordered_runs, signals),
    )


def team_calibration_summary_for_runs(
    store: RunStore,
    runs: list[RunRecord],
    limit: int = 100,
) -> CalibrationProfileSummary:
    return calibration_profile_summary(_signals_for_runs(store, runs, limit=limit))


def calibration_profile_summary(signals: list[CalibrationSignal]) -> CalibrationProfileSummary:
    return CalibrationProfileSummary(
        signal_count=len(signals),
        outcome_counts=_outcome_counts(signals),
        outcome_trend=outcome_trend(signals),
        strongest_cue_tags=_strongest_cue_tags(signals),
        source_refs=_signal_refs(signals),
    )


def outcome_trend(signals: list[CalibrationSignal]) -> CalibrationOutcomeTrend:
    if len(signals) < 2:
        return "insufficient_data"
    counts = _outcome_counts(signals)
    improved = counts.get("improved", 0)
    negative = counts.get("worsened", 0) + counts.get("unsafe_abort", 0)
    if improved > negative + counts.get("no_change", 0):
        return "improving"
    if negative >= improved and negative > 0:
        return "negative"
    return "mixed"


def _signals_for_recommendation_context(
    store: RunStore,
    recommendation: ScenarioRecommendation,
) -> list[CalibrationSignal]:
    signals = store.list_calibration_signals(
        target_soldier_id=recommendation.target_soldier_id,
        task_code=recommendation.target_ids.task_code,
        limit=100,
    )
    if recommendation.intervention_id is None:
        return signals
    return [
        signal
        for signal in signals
        if signal.intervention_id in {None, recommendation.intervention_id}
    ]


def _cue_tags_to_watch(
    recommendation: ScenarioRecommendation,
    signals: list[CalibrationSignal],
) -> list[CalibrationCueTag]:
    from_history = _strongest_cue_tags(signals)
    defaults = _default_cue_tags(recommendation.development_edge)
    selected: list[CalibrationCueTag] = []
    for tag in [*from_history, *defaults]:
        if tag not in selected:
            selected.append(tag)
        if len(selected) >= 3:
            break
    return selected


def _default_cue_tags(edge: DevelopmentEdge) -> list[CalibrationCueTag]:
    mapping = {
        DevelopmentEdge.communications: [
            CalibrationCueTag.communication_timing,
            CalibrationCueTag.team_coordination,
        ],
        DevelopmentEdge.priorities_of_work: [
            CalibrationCueTag.security_posture,
            CalibrationCueTag.fatigue_stress,
        ],
        DevelopmentEdge.fire_control: [
            CalibrationCueTag.fire_control_timing,
            CalibrationCueTag.team_coordination,
        ],
        DevelopmentEdge.leadership_under_fatigue: [
            CalibrationCueTag.leadership_delegation,
            CalibrationCueTag.fatigue_stress,
        ],
        DevelopmentEdge.tactical_patience: [
            CalibrationCueTag.terrain_interaction,
            CalibrationCueTag.team_coordination,
        ],
        DevelopmentEdge.decision_speed: [
            CalibrationCueTag.communication_timing,
            CalibrationCueTag.leadership_delegation,
        ],
        DevelopmentEdge.team_accountability: [
            CalibrationCueTag.team_coordination,
            CalibrationCueTag.security_posture,
        ],
    }
    return mapping.get(edge, [CalibrationCueTag.source_uncertainty])


def _cue_profiles(signals: list[CalibrationSignal]) -> list[CalibrationCueProfile]:
    by_cue: dict[CalibrationCueTag, list[CalibrationSignal]] = defaultdict(list)
    for signal in signals:
        for tag in signal.cue_tags:
            by_cue[tag].append(signal)
    return [
        CalibrationCueProfile(
            cue_tag=tag,
            signal_count=len(items),
            outcome_counts=_outcome_counts(items),
            outcome_trend=outcome_trend(items),
            source_refs=_signal_refs(items),
        )
        for tag, items in sorted(
            by_cue.items(),
            key=lambda item: (-len(item[1]), item[0].value),
        )
    ]


def _intervention_profiles(
    signals: list[CalibrationSignal],
) -> list[CalibrationInterventionProfile]:
    by_intervention: dict[str, list[CalibrationSignal]] = defaultdict(list)
    for signal in signals:
        by_intervention[signal.intervention_id or "unknown_intervention"].append(signal)
    profiles: list[CalibrationInterventionProfile] = []
    for intervention_id, items in sorted(
        by_intervention.items(),
        key=lambda item: (-len(item[1]), item[0]),
    ):
        latest = max(items, key=lambda signal: signal.occurred_at_utc)
        cue_tags = _strongest_cue_tags(items)
        profiles.append(
            CalibrationInterventionProfile(
                intervention_id=intervention_id,
                development_edge=latest.development_edge,
                signal_count=len(items),
                outcome_counts=_outcome_counts(items),
                outcome_trend=outcome_trend(items),
                latest_signal_id=latest.signal_id,
                cue_tags=cue_tags,
                source_refs=_signal_refs(items),
            )
        )
    return profiles


def _development_edge_profiles(
    signals: list[CalibrationSignal],
) -> list[TeamDevelopmentEdgeCalibrationProfile]:
    by_edge: dict[DevelopmentEdge | None, list[CalibrationSignal]] = defaultdict(list)
    for signal in signals:
        by_edge[signal.development_edge].append(signal)
    profiles: list[TeamDevelopmentEdgeCalibrationProfile] = []
    for edge, items in sorted(
        by_edge.items(),
        key=lambda item: (-len(item[1]), item[0].value if item[0] is not None else ""),
    ):
        profiles.append(
            TeamDevelopmentEdgeCalibrationProfile(
                development_edge=edge,
                signal_count=len(items),
                outcome_counts=_outcome_counts(items),
                outcome_trend=outcome_trend(items),
                cue_tags=_strongest_cue_tags(items),
                source_refs=_signal_refs(items),
            )
        )
    return profiles


def _member_summaries(signals: list[CalibrationSignal]) -> list[TeamMemberCalibrationSummary]:
    by_soldier: dict[str, list[CalibrationSignal]] = defaultdict(list)
    for signal in signals:
        by_soldier[signal.target_soldier_id or "unknown_soldier"].append(signal)
    return [
        TeamMemberCalibrationSummary(
            soldier_id=soldier_id,
            signal_count=len(items),
            outcome_counts=_outcome_counts(items),
            outcome_trend=outcome_trend(items),
            strongest_cue_tags=_strongest_cue_tags(items),
            source_refs=_signal_refs(items),
        )
        for soldier_id, items in sorted(
            by_soldier.items(),
            key=lambda item: (-len(item[1]), item[0]),
        )
    ]


def _signals_for_runs(
    store: RunStore,
    runs: list[RunRecord],
    limit: int = 100,
) -> list[CalibrationSignal]:
    seen: dict[str, CalibrationSignal] = {}
    for record in runs:
        for signal in store.list_calibration_signals(run_id=record.run_id, limit=limit):
            seen.setdefault(signal.signal_id, signal)
    return sorted(
        seen.values(),
        key=lambda signal: signal.occurred_at_utc,
        reverse=True,
    )[:limit]


def _soldier_ids_for_runs(
    runs: list[RunRecord],
    signals: list[CalibrationSignal],
) -> list[str]:
    soldier_ids = {observation.soldier_id for record in runs for observation in record.observations}
    soldier_ids.update(
        item.recommendation.target_soldier_id for record in runs for item in record.recommendations
    )
    soldier_ids.update(
        signal.target_soldier_id for signal in signals if signal.target_soldier_id is not None
    )
    return sorted(soldier_ids)


def _team_source_refs(
    runs: list[RunRecord],
    signals: list[CalibrationSignal],
) -> list[str]:
    refs = [f"postgres://ranger_runs/{record.run_id}" for record in runs]
    refs.extend(_signal_refs(signals))
    return sorted(set(refs))


def _team_update_refs(
    runs: list[RunRecord],
    signals: list[CalibrationSignal],
) -> list[str]:
    refs = [f"postgres://ranger_runs/{record.run_id}#record.recommendations" for record in runs]
    refs.extend(f"postgres://ranger_calibration_signals/{signal.signal_id}" for signal in signals)
    return sorted(set(refs))


def _strongest_cue_tags(signals: list[CalibrationSignal]) -> list[CalibrationCueTag]:
    counts: Counter[CalibrationCueTag] = Counter(
        tag for signal in signals for tag in signal.cue_tags
    )
    return [tag for tag, _count in counts.most_common(3)]


def _outcome_counts(signals: list[CalibrationSignal]) -> dict[str, int]:
    counts: Counter[str] = Counter(signal.outcome for signal in signals)
    return dict(sorted(counts.items()))


def _signal_refs(signals: list[CalibrationSignal]) -> list[str]:
    refs = [f"postgres://ranger_calibration_signals/{signal.signal_id}" for signal in signals]
    refs.extend(ref.ref for signal in signals for ref in signal.evidence_refs)
    return sorted(set(refs))


def hydrate_calibration_signal(
    signal: CalibrationSignal,
    entity: EntityRecommendation,
) -> CalibrationSignal:
    recommendation = entity.recommendation
    return signal.model_copy(
        update={
            "run_id": entity.run_id,
            "recommendation_id": recommendation.recommendation_id,
            "target_soldier_id": recommendation.target_soldier_id,
            "task_code": recommendation.target_ids.task_code,
            "development_edge": recommendation.development_edge,
            "intervention_id": recommendation.intervention_id,
        }
    )
