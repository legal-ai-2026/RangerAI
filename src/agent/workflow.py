from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, cast
from uuid import uuid4

from src.agent.cache import RunLease, build_run_lease
from src.agent.graph import (
    RangerState,
    build_ranger_graph,
    extract_state,
    make_resume_command,
)
from src.agent.store import RunStore
from src.contracts import (
    ApprovalResponse,
    AuditEvent,
    IngestEnvelope,
    Observation,
    OutboxEvent,
    RunRecord,
    RunStatus,
    ScenarioRecommendation,
    UpdateLedgerEntry,
)
from src.ingest.providers import ProviderClients
from src.kg.client import KGClient


class RangerWorkflow:
    def __init__(
        self,
        store: RunStore,
        providers: ProviderClients | None = None,
        kg: KGClient | None = None,
        lease: RunLease | None = None,
        graph: Any | None = None,
    ) -> None:
        self.store = store
        self.providers = providers or ProviderClients()
        self.kg = kg or KGClient()
        self.lease = lease or build_run_lease()
        self.graph = graph or build_ranger_graph(providers=self.providers, kg=self.kg)

    def create_run(self, ingest: IngestEnvelope) -> RunRecord:
        record = RunRecord(run_id=str(uuid4()), status=RunStatus.accepted, ingest=ingest)
        self.store.put(record)
        self.store.append_audit_event(
            AuditEvent(
                run_id=record.run_id,
                event_type="run_accepted",
                actor_id=ingest.instructor_id,
                payload={
                    "mission_id": ingest.mission_id,
                    "platoon_id": ingest.platoon_id,
                    "phase": ingest.phase.value,
                },
            )
        )
        return record

    async def process(self, run_id: str) -> None:
        lease = self.lease.acquire(run_id)
        if not lease.acquired:
            record = self._require_run(run_id)
            record.errors.append("run is already being processed")
            self.store.put(record)
            self.store.append_audit_event(
                AuditEvent(
                    run_id=run_id,
                    event_type="run_lease_blocked",
                    actor_id=record.ingest.instructor_id,
                )
            )
            return
        record = self._require_run(run_id)
        try:
            record.status = RunStatus.processing
            self.store.put(record)
            self.store.append_audit_event(
                AuditEvent(
                    run_id=run_id,
                    event_type="run_processing_started",
                    actor_id=record.ingest.instructor_id,
                )
            )
            state: RangerState = {
                "run_id": run_id,
                "ingest": record.ingest,
                "transcript": record.transcript,
                "ocr_pages": record.ocr_pages,
                "observations": record.observations,
                "kg_write_summary": record.kg_write_summary,
                "recommendations": record.recommendations,
                "status": RunStatus.processing,
                "errors": record.errors,
            }
            existing_observation_ids = {item.observation_id for item in record.observations}
            output = await self.graph.ainvoke(state, config=self._graph_config(run_id))
            updated = self._put_state(
                run_id,
                extract_state(output, graph=self.graph, config=self._graph_config(run_id)),
            )
            self._append_observation_updates(updated, existing_observation_ids)
            self.store.append_audit_event(
                AuditEvent(
                    run_id=run_id,
                    event_type="run_status_updated",
                    actor_id=record.ingest.instructor_id,
                    payload={"status": updated.status.value},
                )
            )
        except Exception as exc:
            record = self._require_run(run_id)
            record.status = RunStatus.failed
            record.errors.append(str(exc))
            self.store.put(record)
            self.store.append_audit_event(
                AuditEvent(
                    run_id=run_id,
                    event_type="run_failed",
                    actor_id=record.ingest.instructor_id,
                    payload={"error": str(exc)},
                )
            )
        finally:
            lease.release()

    def approve(
        self,
        run_id: str,
        recommendation_id: str,
        approved: bool,
        edited_recommendation: ScenarioRecommendation | None = None,
    ) -> ApprovalResponse:
        if edited_recommendation is not None and not approved:
            raise ValueError("edited recommendations can only be submitted with approval")
        if (
            edited_recommendation is not None
            and edited_recommendation.recommendation_id != recommendation_id
        ):
            raise ValueError("edited recommendation id must match the decision target")
        lease = self.lease.acquire(run_id)
        if not lease.acquired:
            raise ValueError("run is already being processed")
        try:
            record = self._require_run(run_id)
            for item in record.recommendations:
                if item.recommendation.recommendation_id == recommendation_id:
                    if item.status == "blocked" and approved:
                        raise ValueError("blocked recommendations cannot be approved")
                    break
            else:
                raise KeyError(f"recommendation {recommendation_id} not found")

            resume_payload: dict[str, object] = {
                "recommendation_id": recommendation_id,
                "decision": "approve" if approved else "reject",
            }
            if edited_recommendation is not None:
                resume_payload["edited_recommendation"] = edited_recommendation.model_dump(
                    mode="json"
                )
            command = make_resume_command(resume_payload)
            output = self._invoke_resume(run_id, command)
            updated = self._put_state(
                run_id,
                extract_state(output, graph=self.graph, config=self._graph_config(run_id)),
            )
            for item in updated.recommendations:
                if item.recommendation.recommendation_id == recommendation_id:
                    if item.status not in {"approved", "rejected"}:
                        raise ValueError(
                            f"recommendation decision produced invalid status {item.status}"
                        )
                    decision_status = cast(Literal["approved", "rejected"], item.status)
                    recommendation_update = _recommendation_update_event(
                        run_id=run_id,
                        recommendation=item.recommendation,
                        status=decision_status,
                    )
                    self.store.append_update_event(recommendation_update)
                    self.store.append_audit_event(
                        AuditEvent(
                            run_id=run_id,
                            event_type="recommendation_decision_recorded",
                            actor_id=record.ingest.instructor_id,
                            recommendation_id=recommendation_id,
                            payload={
                                "status": decision_status,
                                "edited": edited_recommendation is not None,
                            },
                        )
                    )
                    event_type: Literal["recommendation.approved", "recommendation.rejected"] = (
                        "recommendation.approved"
                        if decision_status == "approved"
                        else "recommendation.rejected"
                    )
                    self.store.append_outbox_event(
                        OutboxEvent(
                            event_type=event_type,
                            aggregate_id=recommendation_id,
                            run_id=run_id,
                            payload={
                                "recommendation_id": recommendation_id,
                                "status": decision_status,
                                "target_soldier_id": item.recommendation.target_soldier_id,
                                "target_ids": item.recommendation.target_ids.model_dump(
                                    mode="json", exclude_none=True
                                ),
                                "evidence_refs": [
                                    ref.model_dump(mode="json")
                                    for ref in item.recommendation.evidence_refs
                                ],
                                "model_context_refs": list(item.recommendation.model_context_refs),
                                "policy_refs": list(item.recommendation.policy_refs),
                                "edited": edited_recommendation is not None,
                            },
                        )
                    )
                    return ApprovalResponse(
                        run_id=run_id,
                        recommendation_id=recommendation_id,
                        status=decision_status,
                    )
            raise KeyError(f"recommendation {recommendation_id} not found")
        finally:
            lease.release()

    def _require_run(self, run_id: str) -> RunRecord:
        record = self.store.get(run_id)
        if record is None:
            raise KeyError(f"run {run_id} not found")
        return record

    def _invoke_resume(self, run_id: str, command: Any) -> dict[str, Any]:
        import asyncio

        return asyncio.run(self.graph.ainvoke(command, config=self._graph_config(run_id)))

    def _put_state(self, run_id: str, state: RangerState) -> RunRecord:
        record = self._require_run(run_id)
        record.transcript = state.get("transcript")
        record.ocr_pages = state.get("ocr_pages", [])
        record.observations = state.get("observations", [])
        record.kg_write_summary = state.get("kg_write_summary", {})
        record.recommendations = state.get("recommendations", [])
        record.errors = state.get("errors", [])
        record.status = state.get("status", record.status)
        self.store.put(record)
        return record

    def _graph_config(self, run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}}

    def _append_observation_updates(
        self,
        record: RunRecord,
        existing_observation_ids: set[str],
    ) -> None:
        for observation in record.observations:
            if observation.observation_id in existing_observation_ids:
                continue
            self.store.append_update_event(
                _observation_update_event(
                    run_id=record.run_id,
                    observation=observation,
                    graph_name=self.kg.graph_name,
                )
            )


def compile_langgraph_probe() -> bool:
    """Return True when LangGraph is importable in the runtime image."""
    try:
        from langgraph.graph import END, START, StateGraph  # noqa: F401
    except Exception:
        return False
    return True


def _observation_update_event(
    run_id: str,
    observation: Observation,
    graph_name: str,
) -> UpdateLedgerEntry:
    patch = observation.model_dump(mode="json")
    return UpdateLedgerEntry(
        entity_type="observation",
        entity_id=observation.observation_id,
        operation="observe",
        patch=patch,
        source_refs=[
            f"postgres://ranger_runs/{run_id}#record.observations",
            f"falkor://{graph_name}/Observation/{observation.observation_id}",
        ],
        content_hash_after=_content_hash(patch),
    )


def _recommendation_update_event(
    run_id: str,
    recommendation: ScenarioRecommendation,
    status: Literal["approved", "rejected"],
) -> UpdateLedgerEntry:
    patch: dict[str, object] = {
        "recommendation_id": recommendation.recommendation_id,
        "status": status,
        "recommendation": recommendation.model_dump(mode="json"),
        "target_ids": recommendation.target_ids.model_dump(mode="json", exclude_none=True),
        "evidence_refs": [ref.model_dump(mode="json") for ref in recommendation.evidence_refs],
        "model_context_refs": list(recommendation.model_context_refs),
        "policy_refs": list(recommendation.policy_refs),
    }
    operation: Literal["approve", "reject"] = "approve" if status == "approved" else "reject"
    return UpdateLedgerEntry(
        entity_type="recommendation",
        entity_id=recommendation.recommendation_id,
        operation=operation,
        patch=patch,
        source_refs=[
            f"postgres://ranger_runs/{run_id}#record.recommendations",
            *[ref.ref for ref in recommendation.evidence_refs],
        ],
        content_hash_after=_content_hash(patch),
    )


def _content_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
