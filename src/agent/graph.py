from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypedDict, cast

from typing_extensions import NotRequired

from src.agent.policy import PolicyEngine, observations_to_roster
from src.contracts import (
    IngestEnvelope,
    ORBookletPage,
    Observation,
    PolicyDecision,
    RecommendationRecord,
    RunStatus,
    ScenarioRecommendation,
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


class PendingRecommendationCard(TypedDict):
    recommendation_id: str
    target_soldier_id: str
    rationale: str
    proposed_modification: str
    doctrine_refs: list[str]
    risk_level: str
    fairness_score: float
    policy_allowed: bool
    policy_reasons: list[str]


class RangerState(TypedDict):
    run_id: str
    ingest: IngestEnvelope
    transcript: NotRequired[str | None]
    ocr_pages: NotRequired[list[ORBookletPage]]
    observations: NotRequired[list[Observation]]
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
    builder.add_node("reason", nodes.reason_node)
    builder.add_node("policy", nodes.policy_node)
    builder.add_node("human_gate", nodes.human_gate_node)
    builder.add_node("emit", nodes.emit_node)

    builder.add_edge(START, "stt")
    builder.add_edge(START, "ocr")
    builder.add_edge(["stt", "ocr"], "extract")
    builder.add_edge("extract", "kg_write")
    builder.add_edge("kg_write", "reason")
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

    async def stt_node(self, state: RangerState) -> dict[str, Any]:
        ingest = state["ingest"]
        if not ingest.audio_b64:
            return {"transcript": None}
        return {"transcript": await self.providers.transcribe(ingest.audio_b64)}

    async def ocr_node(self, state: RangerState) -> dict[str, Any]:
        ingest = state["ingest"]
        if not ingest.image_b64:
            return {"ocr_pages": []}
        return {"ocr_pages": await self.providers.ocr_pages(ingest.image_b64)}

    async def extract_node(self, state: RangerState) -> dict[str, Any]:
        source_text = "\n".join(
            item
            for item in [
                state["ingest"].free_text,
                state.get("transcript"),
                "\n".join(
                    row.observation_note or ""
                    for page in state.get("ocr_pages", [])
                    for row in page.rows
                ),
            ]
            if item
        )
        observations = await self.providers.extract_observations(source_text)
        return {"observations": observations, "status": RunStatus.processing}

    async def kg_write_node(self, state: RangerState) -> dict[str, Any]:
        errors = list(state.get("errors", []))
        try:
            summary = self.kg.write_observations(state["ingest"], state.get("observations", []))
        except Exception as exc:
            errors.append(f"KG write failed: {exc}")
            summary = {"observations": 0}
        return {"kg_write_summary": summary, "errors": errors}

    async def reason_node(self, state: RangerState) -> dict[str, Any]:
        recommendations = await self.providers.draft_recommendations(state.get("observations", []))
        return {"recommendations": _recommendation_records(recommendations)}

    async def policy_node(self, state: RangerState) -> dict[str, Any]:
        policy = PolicyEngine(observations_to_roster(state.get("observations", [])))
        records: list[RecommendationRecord] = []
        for item in state.get("recommendations", []):
            recommendation = item.recommendation
            decision = policy.evaluate(recommendation)
            recommendation.fairness_score = decision.fairness_score
            records.append(
                RecommendationRecord(
                    recommendation=recommendation,
                    policy=decision,
                    status="pending" if decision.allowed else "blocked",
                )
            )
        return {
            "recommendations": records,
            "status": RunStatus.pending_approval,
            "approval_complete": _all_decided(records),
        }

    async def human_gate_node(self, state: RangerState) -> dict[str, Any]:
        records = list(state.get("recommendations", []))
        if _all_decided(records):
            return {"approval_complete": True, "pending_approval_payload": None}

        payload = _pending_payload(state)
        resume = interrupt(payload)
        decision = _parse_resume(resume)
        records = _apply_decision(records, decision)
        return {
            "recommendations": records,
            "approval_decisions": [*state.get("approval_decisions", []), decision],
            "approval_complete": _all_decided(records),
            "pending_approval_payload": None
            if _all_decided(records)
            else _pending_payload({**state, "recommendations": records}),
            "status": RunStatus.completed if _all_decided(records) else RunStatus.pending_approval,
        }

    async def emit_node(self, state: RangerState) -> dict[str, Any]:
        errors = list(state.get("errors", []))
        for item in state.get("recommendations", []):
            if item.status != "approved":
                continue
            try:
                self.kg.write_recommendation(item.recommendation)
            except Exception as exc:
                errors.append(f"KG recommendation write failed: {exc}")
        return {"status": RunStatus.completed, "errors": errors}


class FallbackRangerGraph:
    """Minimal local stand-in used only when LangGraph is not installed."""

    def __init__(self, providers: ProviderClients, kg: KGClient) -> None:
        self.nodes = RangerGraphNodes(providers=providers, kg=kg)
        self.checkpoints: dict[str, RangerState] = {}

    async def ainvoke(
        self, input_data: RangerState | Any, config: dict[str, Any]
    ) -> dict[str, Any]:
        thread_id = _thread_id(config)
        if _is_command(input_data):
            state = self.checkpoints[thread_id]
            decision = _parse_resume(_command_resume(input_data))
            records = _apply_decision(list(state.get("recommendations", [])), decision)
            state = {
                **state,
                "recommendations": records,
                "approval_decisions": [*state.get("approval_decisions", []), decision],
                "approval_complete": _all_decided(records),
                "status": RunStatus.completed
                if _all_decided(records)
                else RunStatus.pending_approval,
            }
            if _all_decided(records):
                state = _merge_state(state, await self.nodes.emit_node(state))
            else:
                state = _merge_state(state, {"pending_approval_payload": _pending_payload(state)})
            self.checkpoints[thread_id] = state
            return dict(state)

        state = cast(RangerState, dict(input_data))
        state = _merge_state(state, await self.nodes.stt_node(state))
        state = _merge_state(state, await self.nodes.ocr_node(state))
        state = _merge_state(state, await self.nodes.extract_node(state))
        state = _merge_state(state, await self.nodes.kg_write_node(state))
        state = _merge_state(state, await self.nodes.reason_node(state))
        state = _merge_state(state, await self.nodes.policy_node(state))
        state = _merge_state(
            state,
            {
                "pending_approval_payload": None
                if _all_decided(state.get("recommendations", []))
                else _pending_payload(state)
            },
        )
        if _all_decided(state.get("recommendations", [])):
            state = _merge_state(state, await self.nodes.emit_node(state))
        self.checkpoints[thread_id] = state
        return dict(state)

    def get_state(self, config: dict[str, Any]) -> Any:
        class Snapshot:
            def __init__(self, values: RangerState) -> None:
                self.values = values

        return Snapshot(self.checkpoints[_thread_id(config)])


def make_resume_command(payload: ApprovalResume) -> Any:
    if Command is None:
        return {"resume": payload}
    return Command(resume=payload)


def extract_state(output: dict[str, Any], graph: Any, config: dict[str, Any]) -> RangerState:
    if "__interrupt__" in output:
        snapshot = graph.get_state(config)
        values = dict(snapshot.values)
        values["pending_approval_payload"] = _interrupt_payload(output)
        values["status"] = RunStatus.pending_approval
        return cast(RangerState, values)
    return cast(RangerState, output)


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


def _pending_payload(state: RangerState | dict[str, Any]) -> dict[str, Any]:
    cards: list[PendingRecommendationCard] = []
    for item in state.get("recommendations", []):
        if item.status != "pending":
            continue
        recommendation = item.recommendation
        cards.append(
            {
                "recommendation_id": recommendation.recommendation_id,
                "target_soldier_id": recommendation.target_soldier_id,
                "rationale": recommendation.rationale,
                "proposed_modification": recommendation.proposed_modification,
                "doctrine_refs": list(recommendation.doctrine_refs),
                "risk_level": recommendation.risk_level.value,
                "fairness_score": recommendation.fairness_score,
                "policy_allowed": item.policy.allowed,
                "policy_reasons": list(item.policy.reasons),
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
    return {"recommendation_id": recommendation_id, "decision": decision}


def _apply_decision(
    records: list[RecommendationRecord],
    decision: ApprovalResume,
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
        updated.append(
            item.model_copy(
                update={"status": "approved" if decision["decision"] == "approve" else "rejected"}
            )
        )
    if not changed:
        raise KeyError(f"recommendation {decision['recommendation_id']} not found")
    return updated


def _all_decided(records: list[RecommendationRecord]) -> bool:
    return all(item.status in {"approved", "rejected", "blocked"} for item in records)


def _merge_state(state: RangerState, updates: dict[str, Any]) -> RangerState:
    return cast(RangerState, {**state, **updates})


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
