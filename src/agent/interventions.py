from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from src.contracts import (
    DevelopmentEdge,
    Observation,
    RecommendationScore,
    RiskLevel,
    ScenarioRecommendation,
    TargetIds,
)


@dataclass(frozen=True)
class InterventionTemplate:
    intervention_id: str
    task_codes: tuple[str, ...]
    keywords: tuple[str, ...]
    development_edge: DevelopmentEdge
    learning_objective: str
    proposed_modification: str
    doctrine_suffix: str
    estimated_duration_min: int
    requires_resources: tuple[str, ...] = ()


INTERVENTION_LIBRARY: tuple[InterventionTemplate, ...] = (
    InterventionTemplate(
        intervention_id="comm_degraded_sitrep",
        task_codes=("MV-2",),
        keywords=("phase line", "sitrep", "frago", "report", "communication"),
        development_edge=DevelopmentEdge.communications,
        learning_objective="Verify concise reporting and command-and-control discipline under ambiguity.",
        proposed_modification=(
            "At the next halt, require {soldier_id} to issue a five-point SITREP and "
            "short FRAGO through a runner relay with a four-minute limit, then brief back "
            "corrections before movement resumes."
        ),
        doctrine_suffix="MV-2",
        estimated_duration_min=12,
        requires_resources=("runner relay",),
    ),
    InterventionTemplate(
        intervention_id="security_priorities_reset",
        task_codes=("PB-7",),
        keywords=("asleep", "security", "patrol base", "priorities of work", "halt"),
        development_edge=DevelopmentEdge.priorities_of_work,
        learning_objective="Test priorities-of-work discipline without adding unsafe physical stress.",
        proposed_modification=(
            "During the next patrol-base halt, have {soldier_id} run a supervised "
            "security handoff and priorities-of-work check, then identify two controls "
            "that prevent the observed lapse from recurring."
        ),
        doctrine_suffix="PB-7",
        estimated_duration_min=15,
    ),
    InterventionTemplate(
        intervention_id="fire_control_rehearsal",
        task_codes=("AM-4",),
        keywords=("ambush", "initiation", "fire control", "support by fire"),
        development_edge=DevelopmentEdge.fire_control,
        learning_objective="Make fire-control and initiation cues observable in a dry rehearsal.",
        proposed_modification=(
            "Before the next contact drill, have {soldier_id} lead a dry fire-control "
            "rehearsal that names trigger, lift, shift, and cease-fire cues for each element."
        ),
        doctrine_suffix="AM-4",
        estimated_duration_min=18,
    ),
    InterventionTemplate(
        intervention_id="delegation_under_fatigue",
        task_codes=(),
        keywords=("fatigue", "delegat", "leader", "decision", "confusion", "ambiguous"),
        development_edge=DevelopmentEdge.leadership_under_fatigue,
        learning_objective="Assess delegation and decision clarity when the situation is unclear.",
        proposed_modification=(
            "Give {soldier_id} a short ambiguous follow-on task, require one delegated "
            "subordinate report, and have the instructor capture whether intent, task, "
            "and timeline are clear."
        ),
        doctrine_suffix="leadership",
        estimated_duration_min=20,
    ),
)

FATIGUE_TERMS = (
    "asleep",
    "sleep",
    "0300",
    "fatigue",
    "exhaust",
    "cold",
    "wet",
    "shiver",
    "nutrition",
)


def draft_intervention_recommendations(
    observations: list[Observation],
    max_recommendations: int = 3,
) -> list[ScenarioRecommendation]:
    candidates: list[ScenarioRecommendation] = []
    soldier_counts: Counter[str] = Counter()
    edge_counts: Counter[DevelopmentEdge] = Counter()
    soldier_task_counts: Counter[tuple[str, str]] = Counter()

    for observation in observations:
        if not _is_actionable_observation(observation):
            continue
        template = _best_template(observation)
        score = _score_observation(
            observation=observation,
            template=template,
            soldier_counts=soldier_counts,
            edge_counts=edge_counts,
            soldier_task_counts=soldier_task_counts,
        )
        soldier_counts[observation.soldier_id] += 1
        edge_counts[template.development_edge] += 1
        soldier_task_counts[(observation.soldier_id, observation.task_code)] += 1
        candidates.append(_recommendation_from_template(observation, template, score))

    return sorted(
        candidates,
        key=lambda item: item.score_breakdown.total if item.score_breakdown else 0.0,
        reverse=True,
    )[:max_recommendations]


def _is_actionable_observation(observation: Observation) -> bool:
    return observation.soldier_id not in {"", "UNKNOWN"} and observation.rating != "UNCERTAIN"


def _best_template(observation: Observation) -> InterventionTemplate:
    scored = sorted(
        (
            (_template_match_score(template, observation), template)
            for template in INTERVENTION_LIBRARY
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if scored[0][0] <= 0:
        return next(
            template
            for template in INTERVENTION_LIBRARY
            if template.intervention_id == "delegation_under_fatigue"
        )
    return scored[0][1]


def _template_match_score(template: InterventionTemplate, observation: Observation) -> float:
    note = observation.note.lower()
    score = 0.0
    if observation.task_code in template.task_codes:
        score += 3.0
    if observation.task_code != "UNMAPPED" and any(
        observation.task_code.startswith(prefix.rstrip("-")) for prefix in template.task_codes
    ):
        score += 1.0
    score += sum(0.4 for keyword in template.keywords if keyword in note)
    return score


def _score_observation(
    observation: Observation,
    template: InterventionTemplate,
    soldier_counts: Counter[str],
    edge_counts: Counter[DevelopmentEdge],
    soldier_task_counts: Counter[tuple[str, str]],
) -> RecommendationScore:
    fatigue = _fatigue_overload(observation)
    learning_delta = {"NOGO": 0.9, "GO": 0.45, "UNCERTAIN": 0.25}[observation.rating]
    doctrinal_fit = 0.95 if observation.task_code in template.task_codes else 0.7
    instructor_utility = 0.9 if observation.rating == "NOGO" else 0.65
    novelty_bonus = 0.2 if edge_counts[template.development_edge] == 0 else 0.05
    fairness_penalty = min(1.0, soldier_counts[observation.soldier_id] * 0.25)
    repetition_penalty = min(
        1.0,
        soldier_task_counts[(observation.soldier_id, observation.task_code)] * 0.2,
    )
    safety_risk = 0.08 + (0.08 if fatigue > 0.2 else 0.0)
    total = (
        learning_delta
        + doctrinal_fit
        + instructor_utility
        + novelty_bonus
        - safety_risk
        - fatigue
        - fairness_penalty
        - repetition_penalty
    )
    return RecommendationScore(
        learning_delta=learning_delta,
        doctrinal_fit=doctrinal_fit,
        instructor_utility=instructor_utility,
        novelty_bonus=novelty_bonus,
        safety_risk=round(safety_risk, 2),
        fatigue_overload=fatigue,
        fairness_penalty=fairness_penalty,
        repetition_penalty=repetition_penalty,
        total=round(total, 3),
    )


def _recommendation_from_template(
    observation: Observation,
    template: InterventionTemplate,
    score: RecommendationScore,
) -> ScenarioRecommendation:
    doctrine_ref = f"TC 3-21.76 {template.doctrine_suffix}"
    safety_checks = [
        "No immersion, live-fire, unsupervised movement, or punitive physical load added.",
        "Instructor may downgrade or cancel the inject if fatigue, cold, heat, or terrain risk rises.",
    ]
    return ScenarioRecommendation(
        target_soldier_id=observation.soldier_id,
        rationale=(
            f"{observation.soldier_id} produced a {observation.rating} signal on "
            f"{observation.task_code}. The selected library intervention scored "
            f"{score.total:.2f} because it targets the observed development edge while "
            "keeping safety and fairness penalties explicit."
        ),
        development_edge=template.development_edge,
        proposed_modification=template.proposed_modification.format(
            soldier_id=observation.soldier_id
        ),
        doctrine_refs=[doctrine_ref],
        safety_checks=safety_checks,
        estimated_duration_min=template.estimated_duration_min,
        requires_resources=list(template.requires_resources),
        risk_level=RiskLevel.medium if score.fatigue_overload >= 0.25 else RiskLevel.low,
        fairness_score=max(0.0, round(1.0 - score.fairness_penalty, 2)),
        target_ids=TargetIds(soldier_id=observation.soldier_id, task_code=observation.task_code),
        intervention_id=template.intervention_id,
        learning_objective=template.learning_objective,
        score_breakdown=score,
    )


def _fatigue_overload(observation: Observation) -> float:
    note = observation.note.lower()
    if any(term in note for term in FATIGUE_TERMS):
        return 0.28
    return 0.05
