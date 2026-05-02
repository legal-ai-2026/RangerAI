from __future__ import annotations

from collections import Counter

from src.contracts import Observation, PolicyDecision, RiskLevel, ScenarioRecommendation


class PolicyEngine:
    def __init__(self, roster: set[str] | None = None) -> None:
        self.roster = roster or {"Jones", "Smith", "Garcia"}
        self.curveballs: Counter[str] = Counter()

    def evaluate(self, recommendation: ScenarioRecommendation) -> PolicyDecision:
        reasons: list[str] = []
        if recommendation.target_soldier_id not in self.roster:
            reasons.append("target soldier is not in the roster")
        if recommendation.risk_level is RiskLevel.high:
            reasons.append("high-risk recommendations require manual replanning")
        if any("chest-deep" in item.lower() for item in recommendation.safety_checks):
            reasons.append("unsafe cold-water or immersion condition detected")

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
    observed = {item.soldier_id for item in observations if item.soldier_id}
    return observed or {"Jones", "Smith", "Garcia"}
