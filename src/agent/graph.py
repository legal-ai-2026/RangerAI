from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, Literal, TypedDict, cast

from pydantic import BaseModel
from typing_extensions import NotRequired

from src.agent.decision_science import (
    add_decision_support_metadata,
    approval_requires_rationale,
    missing_review_acknowledgements,
)
from src.agent.doctrine import lookup_doctrine_chunks
from src.agent.environment import EnvironmentClients
from src.agent.interventions import draft_intervention_recommendations
from src.agent.policy import PolicyEngine, observations_to_roster
from src.agent.reasoning import (
    ExtractedObservations,
    ExtractionUncertainty,
    ReasoningContext,
    build_reasoning_context,
    extraction_uncertainties_for_observations,
)
from src.contracts import (
    EvidenceRef,
    IngestEnvelope,
    ORBookletPage,
    Observation,
    PolicyDecision,
    RecommendationRecord,
    RunStatus,
    ScenarioRecommendation,
    TargetIds,
)
from src.ingest.providers import ProviderClients
from src.kg.client import KGClient

MemorySaver: Any
StateGraph: Any
Command: Any
START: Any
END: Any
interrupt: Any

try:
    from langgraph.checkpoint.memory import MemorySaver as _ImportedMemorySaver
    from langgraph.graph import END as _ImportedEnd
    from langgraph.graph import START as _ImportedStart
    from langgraph.graph import StateGraph as _ImportedStateGraph
    from langgraph.types import Command as _ImportedCommand
    from langgraph.types import interrupt as _imported_interrupt
except Exception:  # pragma: no cover - dependency probe covers minimal environments
    MemorySaver = None
    StateGraph = None
    Command = None
    START = "__start__"
    END = "__end__"

    def _missing_interrupt(_payload: dict[str, Any]) -> Any:
        raise RuntimeError("langgraph is not installed")

    interrupt = _missing_interrupt
else:
    MemorySaver = _ImportedMemorySaver
    StateGraph = _ImportedStateGraph
    Command = _ImportedCommand
    START = _ImportedStart
    END = _ImportedEnd
    interrupt = _imported_interrupt


ApprovalAction = Literal["approve", "reject"]


class ApprovalResume(TypedDict):
    recommendation_id: str
    decision: ApprovalAction
    edited_recommendation: NotRequired[ScenarioRecommendation]
    decision_rationale: NotRequired[str | None]
    acknowledged_review_requirements: NotRequired[list[str]]


class PendingRecommendationCard(TypedDict):
    recommendation_id: str
    target_soldier_id: str
    target_ids: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    rationale: str
    proposed_modification: str
    doctrine_refs: list[str]
    risk_level: str
    fairness_score: float
    policy_allowed: bool
    policy_reasons: list[str]
    model_context_refs: list[str]
    score_breakdown: NotRequired[dict[str, Any] | None]
    evidence_summary: NotRequired[str | None]
    why_now: NotRequired[str | None]
    expected_learning_signal: NotRequired[str | None]
    risk_controls: NotRequired[str | None]
    uncertainty_refs: NotRequired[list[str]]
    decision_frame: NotRequired[dict[str, Any] | None]
    decision_quality: NotRequired[dict[str, Any] | None]
    value_of_information: NotRequired[dict[str, Any] | None]
    review_requirements: NotRequired[list[dict[str, Any]]]


class RangerState(TypedDict):
    run_id: str
    ingest: IngestEnvelope
    transcript: NotRequired[str | None]
    ocr_pages: NotRequired[list[ORBookletPage]]
    observations: NotRequired[list[Observation]]
    extraction_uncertainties: NotRequired[list[ExtractionUncertainty]]
    reasoning_context: NotRequired[ReasoningContext | None]
    kg_write_summary: NotRequired[dict[str, int]]
    recommendations: NotRequired[list[RecommendationRecord]]
    approval_decisions: NotRequired[list[ApprovalResume]]
    status: NotRequired[RunStatus]
    errors: NotRequired[list[str]]
    pending_approval_payload: NotRequired[dict[str, Any] | None]
    approval_complete: NotRequired[bool]


def build_ranger_graph(
    providers: ProviderClients,
    kg: KGClient,
) -> Any:
    if StateGraph is None or MemorySaver is None:
        return FallbackRangerGraph(providers=providers, kg=kg)

    builder = StateGraph(RangerState)
    nodes = RangerGraphNodes(providers=providers, kg=kg)
    builder.add_node("stt", nodes.stt_node)
    builder.add_node("ocr", nodes.ocr_node)
    builder.add_node("extract", nodes.extract_node)
    builder.add_node("kg_write", nodes.kg_write_node)
    builder.add_node("enrich", nodes.enrich_node)
    builder.add_node("reason", nodes.reason_node)
    builder.add_node("policy", nodes.policy_node)
    builder.add_node("human_gate", nodes.human_gate_node)
    builder.add_node("emit", nodes.emit_node)

    builder.add_edge(START, "stt")
    builder.add_edge(START, "ocr")
    builder.add_edge(["stt", "ocr"], "extract")
    builder.add_edge("extract", "kg_write")
    builder.add_edge("kg_write", "enrich")
    builder.add_edge("enrich", "reason")
    builder.add_edge("reason", "policy")
    builder.add_edge("policy", "human_gate")
    builder.add_conditional_edges(
        "human_gate",
        _approval_route,
        {"pending": "human_gate", "complete": "emit"},
    )
    builder.add_edge("emit", END)
    return builder.compile(checkpointer=MemorySaver())


class RangerGraphNodes:
    def __init__(self, providers: ProviderClients, kg: KGClient) -> None:
        self.providers = providers
        self.kg = kg
        self.environment = EnvironmentClients()

    async def stt_node(self, state: RangerState) -> dict[str, Any]:
        state = _typed_state(state)
        ingest = state["ingest"]
        if not ingest.audio_b64:
            return _json_update({"transcript": None})
        return _json_update({"transcript": await self.providers.transcribe(ingest.audio_b64)})

    async def ocr_node(self, state: RangerState) -> dict[str, Any]:
        state = _typed_state(state)
        ingest = state["ingest"]
        if not ingest.image_b64:
            return _json_update({"ocr_pages": []})
        return _json_update({"ocr_pages": await self.providers.ocr_pages(ingest.image_b64)})

    async def extract_node(self, state: RangerState) -> dict[str, Any]:
        state = _typed_state(state)
        source_text = "\n".join(
            item
            for item in [
                state["ingest"].free_text,
                state.get("transcript"),
            ]
            if item
        )
        extracted = await _extract_observations_with_uncertainty(self.providers, source_text)
        ocr_observations, ocr_uncertainties = _observations_from_ocr_pages(
            state.get("ocr_pages", [])
        )
        observations = _dedupe_observations([*extracted.observations, *ocr_observations])
        uncertainties = [
            *extracted.uncertainties,
            *ocr_uncertainties,
            *extraction_uncertainties_for_observations(
                observations,
                source_ref=f"postgres://ranger_runs/{state['run_id']}#record.observations",
            ),
        ]
        return _json_update(
            {
                "observations": observations,
                "extraction_uncertainties": _dedupe_uncertainties(uncertainties),
                "status": RunStatus.processing,
            }
        )

    async def kg_write_node(self, state: RangerState) -> dict[str, Any]:
        state = _typed_state(state)
        errors = list(state.get("errors", []))
        try:
            summary = self.kg.write_observations(state["ingest"], state.get("observations", []))
        except Exception as exc:
            errors.append(f"KG write failed: {exc}")
            summary = {"observations": 0}
        return _json_update({"kg_write_summary": summary, "errors": errors})

    async def enrich_node(self, state: RangerState) -> dict[str, Any]:
        state = _typed_state(state)
        observations = state.get("observations", [])
        errors = list(state.get("errors", []))
        kg_context_refs = _kg_context_refs(self.kg, observations)
        candidate_refs = sorted(
            {
                ref
                for recommendation in draft_intervention_recommendations(
                    observations,
                    max_recommendations=max(3, min(8, len(observations) or 3)),
                )
                for ref in recommendation.doctrine_refs
            }
        )
        doctrine_chunks = lookup_doctrine_chunks(observations, candidate_refs)
        try:
            weather = self.environment.weather(state["ingest"].geo)
        except Exception as exc:
            errors.append(f"Weather enrichment failed: {exc}")
            weather = None
        try:
            terrain = self.environment.terrain(state["ingest"].geo, state["ingest"].phase)
        except Exception as exc:
            errors.append(f"Terrain enrichment failed: {exc}")
            terrain = None
        context = build_reasoning_context(
            run_id=state["run_id"],
            ingest=state["ingest"],
            observations=observations,
            ocr_pages=state.get("ocr_pages", []),
            kg_observation_refs=kg_context_refs,
            doctrine_chunks=doctrine_chunks,
            weather=weather,
            terrain=terrain,
            extraction_uncertainties=state.get("extraction_uncertainties", []),
            errors=errors,
        )
        return _json_update({"reasoning_context": context, "errors": errors})

    async def reason_node(self, state: RangerState) -> dict[str, Any]:
        state = _typed_state(state)
        observations = state.get("observations", [])
        context = state.get("reasoning_context")
        recommendations = await _draft_recommendations(
            self.providers,
            observations,
            context,
        )
        recommendations = _bind_recommendation_provenance(
            recommendations=recommendations,
            ingest=state["ingest"],
            observations=observations,
            run_id=state["run_id"],
            graph_name=self.kg.graph_name,
            kg_context_refs=context.kg_observation_refs if context else {},
        )
        if context is not None:
            recommendations = _bind_context_refs(recommendations, context.source_refs)
        return _json_update({"recommendations": _recommendation_records(recommendations)})

    async def policy_node(self, state: RangerState) -> dict[str, Any]:
        state = _typed_state(state)
        policy = PolicyEngine(observations_to_roster(state.get("observations", [])))
        context = state.get("reasoning_context")
        records: list[RecommendationRecord] = []
        for item in state.get("recommendations", []):
            recommendation = item.recommendation
            decision = policy.evaluate(recommendation)
            recommendation.fairness_score = decision.fairness_score
            recommendation.policy_refs = _policy_refs(recommendation, decision)
            recommendation = add_decision_support_metadata(
                recommendation,
                decision,
                context,
            )
            records.append(
                RecommendationRecord(
                    recommendation=recommendation,
                    policy=decision,
                    status="pending" if decision.allowed else "blocked",
                )
            )
            if decision.allowed:
                policy.record_approved(recommendation.target_soldier_id)
        return _json_update(
            {
                "recommendations": records,
                "status": RunStatus.pending_approval,
                "approval_complete": _all_decided(records),
            }
        )

    async def human_gate_node(self, state: RangerState) -> dict[str, Any]:
        state = _typed_state(state)
        records = list(state.get("recommendations", []))
        if _all_decided(records):
            return _json_update({"approval_complete": True, "pending_approval_payload": None})

        payload = _pending_payload(state)
        resume = interrupt(payload)
        decision = _parse_resume(resume)
        records = _apply_decision(
            records,
            decision,
            state.get("observations", []),
            state.get("reasoning_context"),
        )
        return _json_update(
            {
                "recommendations": records,
                "approval_decisions": [*state.get("approval_decisions", []), decision],
                "approval_complete": _all_decided(records),
                "pending_approval_payload": None
                if _all_decided(records)
                else _pending_payload({**state, "recommendations": records}),
                "status": RunStatus.completed
                if _all_decided(records)
                else RunStatus.pending_approval,
            }
        )

    async def emit_node(self, state: RangerState) -> dict[str, Any]:
        state = _typed_state(state)
        errors = list(state.get("errors", []))
        for item in state.get("recommendations", []):
            if item.status != "approved":
                continue
            try:
                self.kg.write_recommendation(item.recommendation)
            except Exception as exc:
                errors.append(f"KG recommendation write failed: {exc}")
        return _json_update({"status": RunStatus.completed, "errors": errors})


class FallbackRangerGraph:
    """Minimal local stand-in used only when LangGraph is not installed."""

    def __init__(self, providers: ProviderClients, kg: KGClient) -> None:
        self.nodes = RangerGraphNodes(providers=providers, kg=kg)
        self.checkpoints: dict[str, dict[str, Any]] = {}

    async def ainvoke(
        self, input_data: RangerState | Any, config: dict[str, Any]
    ) -> dict[str, Any]:
        thread_id = _thread_id(config)
        if _is_command(input_data):
            state = _typed_state(self.checkpoints[thread_id])
            decision = _parse_resume(_command_resume(input_data))
            records = _apply_decision(
                list(state.get("recommendations", [])),
                decision,
                state.get("observations", []),
                state.get("reasoning_context"),
            )
            state = _typed_state(
                {
                    **state,
                    "recommendations": records,
                    "approval_decisions": [*state.get("approval_decisions", []), decision],
                    "approval_complete": _all_decided(records),
                    "status": RunStatus.completed
                    if _all_decided(records)
                    else RunStatus.pending_approval,
                }
            )
            if _all_decided(records):
                state = _typed_state(_merge_state(state, await self.nodes.emit_node(state)))
            else:
                state = _typed_state(
                    _merge_state(state, {"pending_approval_payload": _pending_payload(state)})
                )
            self.checkpoints[thread_id] = to_checkpoint_state(state)
            return to_checkpoint_state(state)

        state = _typed_state(input_data)
        state = _typed_state(_merge_state(state, await self.nodes.stt_node(state)))
        state = _typed_state(_merge_state(state, await self.nodes.ocr_node(state)))
        state = _typed_state(_merge_state(state, await self.nodes.extract_node(state)))
        state = _typed_state(_merge_state(state, await self.nodes.kg_write_node(state)))
        state = _typed_state(_merge_state(state, await self.nodes.enrich_node(state)))
        state = _typed_state(_merge_state(state, await self.nodes.reason_node(state)))
        state = _typed_state(_merge_state(state, await self.nodes.policy_node(state)))
        state = _typed_state(
            _merge_state(
                state,
                {
                    "pending_approval_payload": None
                    if _all_decided(state.get("recommendations", []))
                    else _pending_payload(state)
                },
            )
        )
        if _all_decided(state.get("recommendations", [])):
            state = _typed_state(_merge_state(state, await self.nodes.emit_node(state)))
        self.checkpoints[thread_id] = to_checkpoint_state(state)
        return to_checkpoint_state(state)

    def get_state(self, config: dict[str, Any]) -> Any:
        class Snapshot:
            def __init__(self, values: dict[str, Any]) -> None:
                self.values = values

        return Snapshot(self.checkpoints[_thread_id(config)])


def make_resume_command(payload: dict[str, object]) -> Any:
    if Command is None:
        return {"resume": payload}
    return Command(resume=payload)


def extract_state(output: dict[str, Any], graph: Any, config: dict[str, Any]) -> RangerState:
    if "__interrupt__" in output:
        snapshot = graph.get_state(config)
        values = dict(snapshot.values)
        values["pending_approval_payload"] = _interrupt_payload(output)
        values["status"] = RunStatus.pending_approval
        return _typed_state(values)
    return _typed_state(output)


def to_checkpoint_state(state: RangerState | dict[str, Any]) -> dict[str, Any]:
    return _json_state(state)


def _approval_route(state: RangerState) -> str:
    return "complete" if state.get("approval_complete") else "pending"


def _recommendation_records(
    recommendations: list[ScenarioRecommendation],
) -> list[RecommendationRecord]:
    default_policy = PolicyDecision(allowed=True, reasons=[], fairness_score=1.0)
    return [
        RecommendationRecord(recommendation=recommendation, policy=default_policy, status="pending")
        for recommendation in recommendations
    ]


def _bind_recommendation_provenance(
    recommendations: list[ScenarioRecommendation],
    ingest: IngestEnvelope,
    observations: list[Observation],
    run_id: str,
    graph_name: str,
    kg_context_refs: dict[str, list[str]] | None = None,
) -> list[ScenarioRecommendation]:
    observations_by_soldier: dict[str, list[Observation]] = {}
    for observation in observations:
        observations_by_soldier.setdefault(observation.soldier_id, []).append(observation)

    bound: list[ScenarioRecommendation] = []
    for recommendation in recommendations:
        matched = observations_by_soldier.get(recommendation.target_soldier_id, [])
        primary = matched[0] if matched else None
        target_ids = TargetIds(
            soldier_id=recommendation.target_ids.soldier_id or recommendation.target_soldier_id,
            platoon_id=recommendation.target_ids.platoon_id or ingest.platoon_id,
            patrol_id=recommendation.target_ids.patrol_id,
            mission_id=recommendation.target_ids.mission_id or ingest.mission_id,
            task_code=recommendation.target_ids.task_code
            or (primary.task_code if primary else None),
        )
        evidence_refs = list(recommendation.evidence_refs)
        evidence_refs.extend(
            EvidenceRef(
                ref=(f"falkor://{graph_name}/Observation/" f"{observation.observation_id}#note"),
                role="primary_observation",
            )
            for observation in matched
        )
        evidence_refs.extend(
            EvidenceRef(ref=_doctrine_locator(ref), role="doctrine")
            for ref in recommendation.doctrine_refs
        )
        if not evidence_refs:
            evidence_refs.append(
                EvidenceRef(
                    ref=f"postgres://ranger_runs/{run_id}#record.observations",
                    role="model_context",
                )
            )
        model_context_refs = list(recommendation.model_context_refs)
        observation_ref = f"postgres://ranger_runs/{run_id}#record.observations"
        if observation_ref not in model_context_refs:
            model_context_refs.append(observation_ref)
        for ref in (
            kg_context_refs.get(recommendation.target_soldier_id, []) if kg_context_refs else []
        ):
            if ref not in model_context_refs:
                model_context_refs.append(ref)

        bound.append(
            recommendation.model_copy(
                update={
                    "target_ids": target_ids,
                    "evidence_refs": evidence_refs,
                    "model_context_refs": model_context_refs,
                }
            )
        )
    return bound


def _bind_context_refs(
    recommendations: list[ScenarioRecommendation],
    source_refs: list[str],
) -> list[ScenarioRecommendation]:
    bound: list[ScenarioRecommendation] = []
    for recommendation in recommendations:
        refs = list(recommendation.model_context_refs)
        for source_ref in source_refs:
            if source_ref not in refs:
                refs.append(source_ref)
        bound.append(recommendation.model_copy(update={"model_context_refs": refs}))
    return bound


def _doctrine_locator(doctrine_ref: str) -> str:
    slug = doctrine_ref.strip().replace(" ", "-").replace("/", "-").replace("#", "")
    return f"pgvector://doctrine/{slug}"


def _pending_payload(state: RangerState | dict[str, Any]) -> dict[str, Any]:
    state = _typed_state(state)
    cards: list[PendingRecommendationCard] = []
    for item in state.get("recommendations", []):
        if item.status != "pending":
            continue
        recommendation = item.recommendation
        cards.append(
            {
                "recommendation_id": recommendation.recommendation_id,
                "target_soldier_id": recommendation.target_soldier_id,
                "target_ids": recommendation.target_ids.model_dump(mode="json", exclude_none=True),
                "evidence_refs": [
                    ref.model_dump(mode="json") for ref in recommendation.evidence_refs
                ],
                "rationale": recommendation.rationale,
                "proposed_modification": recommendation.proposed_modification,
                "doctrine_refs": list(recommendation.doctrine_refs),
                "risk_level": recommendation.risk_level.value,
                "fairness_score": recommendation.fairness_score,
                "policy_allowed": item.policy.allowed,
                "policy_reasons": list(item.policy.reasons),
                "model_context_refs": list(recommendation.model_context_refs),
                "score_breakdown": recommendation.score_breakdown.model_dump(mode="json")
                if recommendation.score_breakdown
                else None,
                "evidence_summary": recommendation.evidence_summary,
                "why_now": recommendation.why_now,
                "expected_learning_signal": recommendation.expected_learning_signal,
                "risk_controls": recommendation.risk_controls,
                "uncertainty_refs": list(recommendation.uncertainty_refs),
                "decision_frame": recommendation.decision_frame.model_dump(mode="json")
                if recommendation.decision_frame
                else None,
                "decision_quality": recommendation.decision_quality.model_dump(mode="json")
                if recommendation.decision_quality
                else None,
                "value_of_information": recommendation.value_of_information.model_dump(mode="json")
                if recommendation.value_of_information
                else None,
                "review_requirements": [
                    item.model_dump(mode="json") for item in recommendation.review_requirements
                ],
            }
        )
    return {"run_id": state["run_id"], "recommendations": cards}


def _parse_resume(value: Any) -> ApprovalResume:
    if not isinstance(value, dict):
        raise ValueError("approval resume payload must be an object")
    recommendation_id = value.get("recommendation_id")
    decision = value.get("decision")
    if not isinstance(recommendation_id, str) or decision not in {"approve", "reject"}:
        raise ValueError("approval resume payload requires recommendation_id and decision")
    parsed: ApprovalResume = {"recommendation_id": recommendation_id, "decision": decision}
    decision_rationale = value.get("decision_rationale")
    if decision_rationale is not None and not isinstance(decision_rationale, str):
        raise ValueError("decision_rationale must be a string when provided")
    if decision_rationale is not None:
        parsed["decision_rationale"] = decision_rationale
    acknowledged = value.get("acknowledged_review_requirements", [])
    if not isinstance(acknowledged, list) or not all(
        isinstance(item, str) for item in acknowledged
    ):
        raise ValueError("acknowledged_review_requirements must be a list of strings")
    parsed["acknowledged_review_requirements"] = acknowledged
    edited_value = value.get("edited_recommendation")
    if edited_value is None:
        return parsed
    if decision != "approve":
        raise ValueError("edited recommendations can only be approved")
    try:
        edited = (
            edited_value
            if isinstance(edited_value, ScenarioRecommendation)
            else ScenarioRecommendation.model_validate(edited_value)
        )
    except Exception as exc:
        raise ValueError("edited recommendation payload is invalid") from exc
    if edited.recommendation_id != recommendation_id:
        raise ValueError("edited recommendation id must match the decision target")
    parsed["edited_recommendation"] = edited
    return parsed


def _apply_decision(
    records: list[RecommendationRecord],
    decision: ApprovalResume,
    observations: list[Observation] | None = None,
    context: ReasoningContext | None = None,
) -> list[RecommendationRecord]:
    changed = False
    updated: list[RecommendationRecord] = []
    for item in records:
        if item.recommendation.recommendation_id != decision["recommendation_id"]:
            updated.append(item)
            continue
        changed = True
        if item.status == "blocked" and decision["decision"] == "approve":
            raise ValueError("blocked recommendations cannot be approved")
        if item.status == "blocked":
            updated.append(item)
            continue
        recommendation = item.recommendation
        policy = item.policy
        if "edited_recommendation" in decision:
            recommendation = _merge_instructor_edit(
                item.recommendation,
                decision["edited_recommendation"],
            )
            policy = PolicyEngine(observations_to_roster(observations or [])).evaluate(
                recommendation
            )
            recommendation = recommendation.model_copy(
                update={"fairness_score": policy.fairness_score}
            )
            if not policy.allowed:
                reasons = "; ".join(policy.reasons)
                raise ValueError(f"edited recommendation failed policy: {reasons}")
            recommendation = add_decision_support_metadata(recommendation, policy, context)
        if decision["decision"] == "approve":
            _validate_approval_review_requirements(
                recommendation,
                decision,
                edited="edited_recommendation" in decision,
            )
        updated.append(
            item.model_copy(
                update={
                    "recommendation": recommendation,
                    "policy": policy,
                    "status": "approved" if decision["decision"] == "approve" else "rejected",
                }
            )
        )
    if not changed:
        raise KeyError(f"recommendation {decision['recommendation_id']} not found")
    return updated


def _validate_approval_review_requirements(
    recommendation: ScenarioRecommendation,
    decision: ApprovalResume,
    *,
    edited: bool,
) -> None:
    acknowledged = decision.get("acknowledged_review_requirements", [])
    missing = missing_review_acknowledgements(recommendation, acknowledged)
    if missing:
        raise ValueError(
            "approval requires acknowledged review requirements: " + ", ".join(missing)
        )
    rationale = (decision.get("decision_rationale") or "").strip()
    if approval_requires_rationale(recommendation, edited=edited) and not rationale:
        raise ValueError("approval requires decision_rationale")


def _merge_instructor_edit(
    original: ScenarioRecommendation,
    edited: ScenarioRecommendation,
) -> ScenarioRecommendation:
    target_ids = TargetIds(
        soldier_id=edited.target_ids.soldier_id or edited.target_soldier_id,
        platoon_id=edited.target_ids.platoon_id or original.target_ids.platoon_id,
        patrol_id=edited.target_ids.patrol_id or original.target_ids.patrol_id,
        mission_id=edited.target_ids.mission_id or original.target_ids.mission_id,
        task_code=edited.target_ids.task_code or original.target_ids.task_code,
    )
    return edited.model_copy(
        update={
            "recommendation_id": original.recommendation_id,
            "target_ids": target_ids,
            "evidence_refs": edited.evidence_refs or original.evidence_refs,
            "model_context_refs": edited.model_context_refs or original.model_context_refs,
            "policy_refs": edited.policy_refs or original.policy_refs,
            "intervention_id": edited.intervention_id or original.intervention_id,
            "learning_objective": edited.learning_objective or original.learning_objective,
            "score_breakdown": edited.score_breakdown or original.score_breakdown,
            "decision_frame": edited.decision_frame or original.decision_frame,
            "decision_quality": edited.decision_quality or original.decision_quality,
            "value_of_information": edited.value_of_information or original.value_of_information,
            "review_requirements": edited.review_requirements or original.review_requirements,
            "created_by": "instructor",
        }
    )


def _all_decided(records: list[RecommendationRecord]) -> bool:
    return all(item.status in {"approved", "rejected", "blocked"} for item in records)


def _merge_state(state: RangerState, updates: dict[str, Any]) -> RangerState:
    return cast(RangerState, {**state, **updates})


def _typed_state(raw: RangerState | dict[str, Any]) -> RangerState:
    state = dict(raw)
    ingest = state.get("ingest")
    if ingest is not None and not isinstance(ingest, IngestEnvelope):
        state["ingest"] = IngestEnvelope.model_validate(ingest)
    state["ocr_pages"] = [
        item if isinstance(item, ORBookletPage) else ORBookletPage.model_validate(item)
        for item in state.get("ocr_pages", [])
    ]
    state["observations"] = [
        item if isinstance(item, Observation) else Observation.model_validate(item)
        for item in state.get("observations", [])
    ]
    state["extraction_uncertainties"] = [
        item
        if isinstance(item, ExtractionUncertainty)
        else ExtractionUncertainty.model_validate(item)
        for item in state.get("extraction_uncertainties", [])
    ]
    reasoning_context = state.get("reasoning_context")
    if reasoning_context is not None and not isinstance(reasoning_context, ReasoningContext):
        state["reasoning_context"] = ReasoningContext.model_validate(reasoning_context)
    state["recommendations"] = [
        item
        if isinstance(item, RecommendationRecord)
        else RecommendationRecord.model_validate(item)
        for item in state.get("recommendations", [])
    ]
    status = state.get("status")
    if isinstance(status, str):
        state["status"] = RunStatus(status)
    state["approval_decisions"] = [
        _typed_approval_resume(item) for item in state.get("approval_decisions", [])
    ]
    return cast(RangerState, state)


def _typed_approval_resume(value: ApprovalResume | dict[str, Any]) -> ApprovalResume:
    decision = _parse_resume(value)
    if "edited_recommendation" in decision and not isinstance(
        decision["edited_recommendation"], ScenarioRecommendation
    ):
        decision["edited_recommendation"] = ScenarioRecommendation.model_validate(
            decision["edited_recommendation"]
        )
    return decision


def _json_update(update: dict[str, Any]) -> dict[str, Any]:
    return _json_state(update)


def _json_state(state: RangerState | dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], _jsonable(dict(state)))


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _kg_context_refs(kg: KGClient, observations: list[Observation]) -> dict[str, list[str]]:
    soldier_ids = sorted({observation.soldier_id for observation in observations})
    if not soldier_ids or not hasattr(kg, "recent_observation_refs"):
        return {}
    try:
        refs = kg.recent_observation_refs(soldier_ids)
    except Exception:
        return {}
    return refs if isinstance(refs, dict) else {}


async def _extract_observations_with_uncertainty(
    providers: ProviderClients,
    source_text: str,
) -> ExtractedObservations:
    if not source_text.strip():
        return ExtractedObservations()
    method = getattr(providers, "extract_observations_with_uncertainty", None)
    if callable(method):
        extracted = await method(source_text)
        if isinstance(extracted, ExtractedObservations):
            return extracted
        return ExtractedObservations.model_validate(extracted)
    observations = await providers.extract_observations(source_text)
    return ExtractedObservations(
        observations=observations,
        uncertainties=extraction_uncertainties_for_observations(
            observations,
            source_ref="provider://extract_observations",
        ),
    )


async def _draft_recommendations(
    providers: ProviderClients,
    observations: list[Observation],
    context: ReasoningContext | None,
) -> list[ScenarioRecommendation]:
    method = providers.draft_recommendations
    signature = inspect.signature(method)
    if "reasoning_context" in signature.parameters:
        return await method(observations, reasoning_context=context)
    return await method(observations)


def _observations_from_ocr_pages(
    pages: list[ORBookletPage],
) -> tuple[list[Observation], list[ExtractionUncertainty]]:
    observations: list[Observation] = []
    uncertainties: list[ExtractionUncertainty] = []
    for page_index, page in enumerate(pages):
        page_ref = f"ocr://page/{page.page_id}"
        low_confidence = page.confidence < 0.65
        if low_confidence:
            uncertainties.append(
                ExtractionUncertainty(
                    source_ref=page_ref,
                    uncertainty_type="ocr_low_confidence",
                    confidence=page.confidence,
                    note=f"OCR page confidence {page.confidence:.2f} requires instructor review.",
                )
            )
        for row_index, row in enumerate(page.rows):
            note = row.observation_note or row.task_name or row.task_code
            soldier_id = _soldier_id_from_ocr_note(note)
            rating = "UNCERTAIN" if low_confidence else row.rating
            uncertainty_refs = []
            row_ref = f"{page_ref}#rows[{row_index}]"
            if low_confidence or rating == "UNCERTAIN" or soldier_id == "UNKNOWN":
                uncertainty_refs.append(row_ref)
                uncertainties.append(
                    ExtractionUncertainty(
                        source_ref=row_ref,
                        uncertainty_type="uncertain_rating"
                        if rating == "UNCERTAIN"
                        else "unknown_soldier",
                        confidence=page.confidence,
                        note=note[:700],
                        soldier_id=soldier_id,
                        task_code=row.task_code,
                    )
                )
            observations.append(
                Observation(
                    soldier_id=soldier_id,
                    task_code=row.task_code or "OR-UNCERTAIN",
                    note=note[:1200],
                    rating=rating,
                    source="image",
                    confidence=page.confidence,
                    uncertainty_refs=uncertainty_refs,
                )
            )
        if not page.rows:
            uncertainties.append(
                ExtractionUncertainty(
                    source_ref=f"{page_ref}#rows",
                    uncertainty_type="ocr_low_confidence",
                    confidence=page.confidence,
                    note=f"OCR page {page_index} returned no rows.",
                )
            )
    return observations, uncertainties


def _soldier_id_from_ocr_note(note: str) -> str:
    known = ("Jones", "Smith", "Garcia")
    for soldier_id in known:
        if soldier_id.lower() in note.lower():
            return soldier_id
    match = re.search(r"\b([A-Z][A-Za-z0-9_-]{1,31})\b", note)
    if match:
        candidate = match.group(1)
        if candidate.upper() not in {"GO", "NOGO", "OR", "SITREP", "FRAGO"}:
            return candidate
    return "UNKNOWN"


def _dedupe_observations(observations: list[Observation]) -> list[Observation]:
    unique: dict[tuple[str, str, str, str], Observation] = {}
    for observation in observations:
        key = (
            observation.soldier_id,
            observation.task_code,
            observation.rating,
            observation.note.strip().lower()[:160],
        )
        if key not in unique:
            unique[key] = observation
            continue
        existing = unique[key]
        uncertainty_refs = sorted(
            set(existing.uncertainty_refs) | set(observation.uncertainty_refs)
        )
        confidence_values = [
            value for value in [existing.confidence, observation.confidence] if value is not None
        ]
        unique[key] = existing.model_copy(
            update={
                "confidence": min(confidence_values) if confidence_values else None,
                "uncertainty_refs": uncertainty_refs,
            }
        )
    return list(unique.values())


def _dedupe_uncertainties(
    uncertainties: list[ExtractionUncertainty],
) -> list[ExtractionUncertainty]:
    unique: dict[tuple[str, str, str | None, str | None], ExtractionUncertainty] = {}
    for uncertainty in uncertainties:
        key = (
            uncertainty.source_ref,
            uncertainty.uncertainty_type,
            uncertainty.soldier_id,
            uncertainty.task_code,
        )
        unique.setdefault(key, uncertainty)
    return list(unique.values())


def _policy_refs(
    recommendation: ScenarioRecommendation,
    decision: PolicyDecision,
) -> list[str]:
    refs = list(recommendation.policy_refs)
    refs.append("policy:system1-deterministic-gate")
    refs.extend(f"policy:block:{reason.replace(' ', '-')}" for reason in decision.reasons)
    return sorted(set(refs))


def _thread_id(config: dict[str, Any]) -> str:
    return str(config.get("configurable", {}).get("thread_id", "default"))


def _is_command(value: Any) -> bool:
    return hasattr(value, "resume") or (isinstance(value, dict) and set(value) == {"resume"})


def _command_resume(value: Any) -> Any:
    if isinstance(value, dict):
        return value["resume"]
    return value.resume


def _interrupt_payload(output: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = output.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0]
    if isinstance(first, dict):
        return first.get("value")
    return getattr(first, "value", None)


AsyncNode = Callable[[RangerState], Awaitable[dict[str, Any]]]
