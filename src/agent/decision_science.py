from __future__ import annotations

from typing import Literal

from src.agent.reasoning import ReasoningContext
from src.contracts import (
    DecisionFrame,
    DecisionQuality,
    PolicyDecision,
    ReviewRequirement,
    RiskLevel,
    ScenarioRecommendation,
    ValueOfInformation,
)

MEDIUM_RISK_ACK = "medium_risk_ack"
HIGH_UNCERTAINTY_REVIEW = "high_uncertainty_review"
LOW_OBSERVABILITY_REVIEW = "low_observability_review"
CONTEXT_DEGRADED_REVIEW = "context_degraded_review"


def add_decision_support_metadata(
    recommendation: ScenarioRecommendation,
    policy: PolicyDecision,
    context: ReasoningContext | None,
) -> ScenarioRecommendation:
    quality = _decision_quality(recommendation, policy, context)
    requirements = _review_requirements(recommendation, context)
    return recommendation.model_copy(
        update={
            "decision_frame": _decision_frame(recommendation, context),
            "decision_quality": quality,
            "value_of_information": _value_of_information(quality, requirements),
            "review_requirements": requirements,
        }
    )


def required_review_requirement_ids(
    recommendation: ScenarioRecommendation,
) -> list[str]:
    return sorted(
        {
            item.requirement_id
            for item in recommendation.review_requirements
            if item.required_for_approval
        }
    )


def missing_review_acknowledgements(
    recommendation: ScenarioRecommendation,
    acknowledged: list[str],
) -> list[str]:
    acknowledged_set = set(acknowledged)
    return [
        requirement_id
        for requirement_id in required_review_requirement_ids(recommendation)
        if requirement_id not in acknowledged_set
    ]


def approval_requires_rationale(
    recommendation: ScenarioRecommendation,
    *,
    edited: bool,
) -> bool:
    return edited or bool(required_review_requirement_ids(recommendation))


def _decision_frame(
    recommendation: ScenarioRecommendation,
    context: ReasoningContext | None,
) -> DecisionFrame:
    task_code = recommendation.target_ids.task_code or "observed task"
    constraints = [
        "Instructor approval is mandatory before emit.",
        "Policy filter must allow the recommendation.",
        "No unsafe immersion, live-fire escalation, punitive load, or unsupervised movement.",
        "Recommendation must stay linked to doctrine and observed evidence.",
    ]
    uncertainties = _primary_uncertainties(recommendation, context)
    return DecisionFrame(
        objective=(
            f"Improve {recommendation.target_soldier_id}'s observable performance on "
            f"{task_code} without adding unsafe or unfair training load."
        ),
        constraints=constraints,
        alternatives_considered=[
            "Take no immediate training inject.",
            "Collect more source evidence before deciding.",
            "Use the lower-risk supervised version of the proposed modification.",
            recommendation.proposed_modification[:220],
        ],
        time_pressure="urgent" if recommendation.estimated_duration_min <= 10 else "compressed",
        reversibility=_reversibility(recommendation.risk_level),
        primary_uncertainties=uncertainties,
    )


def _decision_quality(
    recommendation: ScenarioRecommendation,
    policy: PolicyDecision,
    context: ReasoningContext | None,
) -> DecisionQuality:
    score = recommendation.score_breakdown
    context_confidence = context.context_confidence if context is not None else 0.8
    uncertainty_penalty = score.uncertainty_penalty if score is not None else 0.25
    observability = score.observability if score is not None else 0.35
    safety_risk = score.safety_risk if score is not None else 0.2
    fatigue = score.fatigue_overload if score is not None else 0.1
    learning_delta = score.learning_delta if score is not None else 0.5
    doctrinal_fit = score.doctrinal_fit if score is not None else 0.6
    instructor_utility = score.instructor_utility if score is not None else 0.5

    information_quality = _clamp(min(context_confidence, 1.0 - uncertainty_penalty))
    safety_margin = _clamp(1.0 - max(safety_risk, fatigue, _risk_level_penalty(recommendation)))
    fairness_margin = _clamp(policy.fairness_score)
    learning_utility = _clamp((learning_delta + doctrinal_fit + instructor_utility) / 3)
    reliance_risk = _clamp(
        max(
            uncertainty_penalty,
            1.0 - observability,
            1.0 - context_confidence,
            _risk_level_penalty(recommendation),
        )
    )
    overall = _clamp(
        (information_quality * 0.25)
        + (safety_margin * 0.2)
        + (fairness_margin * 0.15)
        + (observability * 0.15)
        + (learning_utility * 0.25)
        - (reliance_risk * 0.2)
    )
    return DecisionQuality(
        information_quality=round(information_quality, 2),
        safety_margin=round(safety_margin, 2),
        fairness_margin=round(fairness_margin, 2),
        observability=round(_clamp(observability), 2),
        learning_utility=round(learning_utility, 2),
        reliance_risk=round(reliance_risk, 2),
        overall=round(overall, 2),
        rating=_quality_rating(overall, reliance_risk),
    )


def _review_requirements(
    recommendation: ScenarioRecommendation,
    context: ReasoningContext | None,
) -> list[ReviewRequirement]:
    score = recommendation.score_breakdown
    requirements: list[ReviewRequirement] = []
    if recommendation.risk_level == RiskLevel.medium:
        requirements.append(
            ReviewRequirement(
                requirement_id=MEDIUM_RISK_ACK,
                reason="Medium-risk training modifications require explicit instructor acknowledgement.",
            )
        )
    if score is not None and score.uncertainty_penalty >= 0.45:
        requirements.append(
            ReviewRequirement(
                requirement_id=HIGH_UNCERTAINTY_REVIEW,
                reason="Observation uncertainty is high enough to require source review before approval.",
            )
        )
    if score is not None and score.observability <= 0.35:
        requirements.append(
            ReviewRequirement(
                requirement_id=LOW_OBSERVABILITY_REVIEW,
                reason="The expected learning signal may be difficult to observe reliably.",
            )
        )
    if context is not None and context.context_confidence < 0.7:
        requirements.append(
            ReviewRequirement(
                requirement_id=CONTEXT_DEGRADED_REVIEW,
                reason="Reasoning context confidence is degraded by uncertainty or upstream errors.",
            )
        )
    return _dedupe_requirements(requirements)


def _value_of_information(
    quality: DecisionQuality,
    requirements: list[ReviewRequirement],
) -> ValueOfInformation:
    collect_more = any(
        item.requirement_id
        in {HIGH_UNCERTAINTY_REVIEW, LOW_OBSERVABILITY_REVIEW, CONTEXT_DEGRADED_REVIEW}
        for item in requirements
    )
    if collect_more:
        return ValueOfInformation(
            collect_more=True,
            reason=(
                "Additional source review can materially reduce uncertainty before the "
                "instructor approves the inject."
            ),
            suggested_action=(
                "Review cited observations, OCR/source confidence, and instructor notes before approval."
            ),
        )
    if quality.reliance_risk >= 0.55:
        return ValueOfInformation(
            collect_more=True,
            reason="Reliance risk is elevated enough to justify an instructor cross-check.",
            suggested_action="Confirm the evidence summary and risk controls before approval.",
        )
    return ValueOfInformation(
        collect_more=False,
        reason="Available evidence is sufficient for normal instructor review.",
        suggested_action=None,
    )


def _primary_uncertainties(
    recommendation: ScenarioRecommendation,
    context: ReasoningContext | None,
) -> list[str]:
    uncertainties: list[str] = []
    if recommendation.uncertainty_refs:
        uncertainties.append("Recommendation is linked to uncertain source evidence.")
    score = recommendation.score_breakdown
    if score is not None and score.uncertainty_penalty >= 0.45:
        uncertainties.append("Score breakdown shows elevated uncertainty penalty.")
    if score is not None and score.observability <= 0.35:
        uncertainties.append("Expected learning signal has low observability.")
    if context is not None and context.context_confidence < 0.7:
        uncertainties.append("Reasoning context confidence is degraded.")
    if context is not None and context.weather is not None and context.weather.synthetic:
        uncertainties.append("Weather context is synthetic and should be verified for live use.")
    if context is not None and context.terrain is not None and context.terrain.synthetic:
        uncertainties.append("Terrain context is synthetic and should be verified for live use.")
    if context is not None and context.terrain is not None and context.terrain.hazards:
        uncertainties.append(
            "Terrain context includes hazards: " + ", ".join(context.terrain.hazards[:3]) + "."
        )
    if context is not None:
        target_uncertainties = [
            item.note
            for item in context.extraction_uncertainties
            if item.soldier_id in {None, recommendation.target_soldier_id}
            and item.task_code in {None, recommendation.target_ids.task_code}
        ]
        uncertainties.extend(target_uncertainties[:2])
    if not uncertainties:
        uncertainties.append("No material extraction or context uncertainty identified.")
    return sorted(set(uncertainties))[:5]


def _reversibility(
    risk_level: RiskLevel,
) -> Literal["reversible", "partially_reversible", "hard_to_reverse"]:
    if risk_level == RiskLevel.high:
        return "hard_to_reverse"
    if risk_level == RiskLevel.medium:
        return "partially_reversible"
    return "reversible"


def _risk_level_penalty(recommendation: ScenarioRecommendation) -> float:
    if recommendation.risk_level == RiskLevel.high:
        return 0.9
    if recommendation.risk_level == RiskLevel.medium:
        return 0.35
    return 0.1


def _quality_rating(overall: float, reliance_risk: float) -> Literal["strong", "review", "weak"]:
    if overall >= 0.72 and reliance_risk < 0.45:
        return "strong"
    if overall >= 0.45:
        return "review"
    return "weak"


def _dedupe_requirements(requirements: list[ReviewRequirement]) -> list[ReviewRequirement]:
    deduped: dict[str, ReviewRequirement] = {}
    for requirement in requirements:
        deduped.setdefault(requirement.requirement_id, requirement)
    return list(deduped.values())


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
