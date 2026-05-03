from src.agent.policy import PolicyEngine
from src.contracts import DevelopmentEdge, RecommendationScore, RiskLevel, ScenarioRecommendation


def recommendation(target: str, risk: RiskLevel = RiskLevel.low) -> ScenarioRecommendation:
    return ScenarioRecommendation(
        target_soldier_id=target,
        rationale="A focused development event is warranted by repeated observed task friction.",
        development_edge=DevelopmentEdge.communications,
        proposed_modification="Run a supervised five-point SITREP drill at the next halt.",
        doctrine_refs=["TC 3-21.76 MV-2"],
        safety_checks=["No immersion added."],
        estimated_duration_min=10,
        requires_resources=[],
        risk_level=risk,
        fairness_score=1.0,
    )


def test_policy_rejects_hallucinated_soldier() -> None:
    decision = PolicyEngine(roster={"Jones"}).evaluate(recommendation("Smith"))
    assert not decision.allowed
    assert "target soldier is not in the roster" in decision.reasons


def test_policy_blocks_high_risk() -> None:
    decision = PolicyEngine(roster={"Jones"}).evaluate(recommendation("Jones", RiskLevel.high))
    assert not decision.allowed
    assert "high-risk recommendations require manual replanning" in decision.reasons


def test_policy_blocks_cold_water_immersion_language() -> None:
    unsafe = recommendation("Jones").model_copy(
        update={"safety_checks": ["Avoid this chest-deep cold-water crossing."]}
    )
    decision = PolicyEngine(roster={"Jones"}).evaluate(unsafe)
    assert not decision.allowed
    assert "unsafe cold-water or immersion condition detected" in decision.reasons


def test_policy_blocks_high_uncertainty_recommendation() -> None:
    uncertain = recommendation("Jones").model_copy(
        update={
            "score_breakdown": RecommendationScore(
                learning_delta=0.8,
                doctrinal_fit=0.8,
                instructor_utility=0.7,
                observability=0.4,
                novelty_bonus=0.1,
                safety_risk=0.1,
                fatigue_overload=0.1,
                uncertainty_penalty=0.85,
                fairness_penalty=0.0,
                repetition_penalty=0.0,
                total=0.95,
            )
        }
    )
    decision = PolicyEngine(roster={"Jones"}).evaluate(uncertain)
    assert not decision.allowed
    assert "observation uncertainty exceeds display threshold" in decision.reasons


def test_policy_suppresses_excessive_repeat_targeting() -> None:
    policy = PolicyEngine(roster={"Jones", "Smith"})
    policy.record_approved("Jones")
    policy.record_approved("Jones")

    decision = policy.evaluate(recommendation("Jones"))

    assert not decision.allowed
    assert "fairness counter would exceed platoon spread threshold" in decision.reasons
