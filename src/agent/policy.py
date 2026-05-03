from __future__ import annotations

import re
from collections import Counter

from src.contracts import Observation, PolicyDecision, RiskLevel, ScenarioRecommendation

UNSAFE_COLD_WATER_TERMS = (
    "chest-deep",
    "cold-water",
    "cold water",
    "submerge",
    "hypothermia",
)

UNSUPPORTED_AUTHORITY_TERMS = (
    "punish",
    "punitive",
    "smoke session",
    "corrective physical training",
    "unapproved stress",
)


class PolicyEngine:
    def __init__(self, roster: set[str] | None = None) -> None:
        self.roster = roster or {"Jones", "Smith", "Garcia"}
        self.curveballs: Counter[str] = Counter()

    def evaluate(self, recommendation: ScenarioRecommendation) -> PolicyDecision:
        reasons: list[str] = []
        if recommendation.target_soldier_id not in self.roster:
            reasons.append("target soldier is not in the roster")
        if recommendation.risk_level == RiskLevel.high:
            reasons.append("high-risk recommendations require manual replanning")
        review_text = " ".join(
            [
                recommendation.rationale,
                recommendation.proposed_modification,
                *recommendation.safety_checks,
                recommendation.risk_controls or "",
            ]
        ).lower()
        unsafe_immersion = (
            "immersion" in review_text
            and "no immersion" not in review_text
            and "without immersion" not in review_text
        )
        if any(term in review_text for term in UNSAFE_COLD_WATER_TERMS) or unsafe_immersion:
            reasons.append("unsafe cold-water or immersion condition detected")
        if _contains_unnegated_term(review_text, UNSUPPORTED_AUTHORITY_TERMS):
            reasons.append("unsupported instructor authority or punitive language detected")
        if recommendation.doctrine_refs and not any(
            ref.startswith("TC 3-21.76") for ref in recommendation.doctrine_refs
        ):
            reasons.append("unsupported doctrine authority detected")
        if "policy:phase-mismatch" in recommendation.policy_refs:
            reasons.append("unsupported phase mismatch detected")
        if recommendation.score_breakdown is not None:
            if recommendation.score_breakdown.uncertainty_penalty >= 0.75:
                reasons.append("observation uncertainty exceeds display threshold")
            if recommendation.score_breakdown.observability <= 0.2:
                reasons.append("recommendation lacks observable evaluation signal")

        projected = self.curveballs.copy()
        projected[recommendation.target_soldier_id] += 1
        fairness_score = self._fairness_score(projected)
        if projected and max(projected.values()) - min(projected.values()) > 2:
            reasons.append("fairness counter would exceed platoon spread threshold")

        return PolicyDecision(
            allowed=not reasons,
            reasons=reasons,
            fairness_score=fairness_score,
        )

    def record_approved(self, soldier_id: str) -> None:
        self.curveballs[soldier_id] += 1

    def _fairness_score(self, counts: Counter[str]) -> float:
        for soldier_id in self.roster:
            counts.setdefault(soldier_id, 0)
        if not counts:
            return 1.0
        spread = max(counts.values()) - min(counts.values())
        return max(0.0, 1.0 - (spread / 3.0))


def observations_to_roster(observations: list[Observation]) -> set[str]:
    observed = {
        item.soldier_id for item in observations if item.soldier_id and item.soldier_id != "UNKNOWN"
    }
    return observed or {"Jones", "Smith", "Garcia"}


def _contains_unnegated_term(text: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        if term not in text:
            continue
        if re.search(rf"\b(no|without)\b[^.]*{re.escape(term)}", text):
            continue
        return True
    return False
