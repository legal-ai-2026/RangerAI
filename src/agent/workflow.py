from __future__ import annotations

from typing import Any
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
    IngestEnvelope,
    RunRecord,
    RunStatus,
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
    ) -> None:
        self.store = store
        self.providers = providers or ProviderClients()
        self.kg = kg or KGClient()
        self.lease = lease or build_run_lease()
        self.graph = build_ranger_graph(providers=self.providers, kg=self.kg)

    def create_run(self, ingest: IngestEnvelope) -> RunRecord:
        record = RunRecord(run_id=str(uuid4()), status=RunStatus.accepted, ingest=ingest)
        self.store.put(record)
        return record

    async def process(self, run_id: str) -> None:
        lease = self.lease.acquire(run_id)
        if not lease.acquired:
            record = self._require_run(run_id)
            record.errors.append("run is already being processed")
            self.store.put(record)
            return
        record = self._require_run(run_id)
        try:
            record.status = RunStatus.processing
            self.store.put(record)
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
            output = await self.graph.ainvoke(state, config=self._graph_config(run_id))
            self._put_state(
                run_id,
                extract_state(output, graph=self.graph, config=self._graph_config(run_id)),
            )
        except Exception as exc:
            record = self._require_run(run_id)
            record.status = RunStatus.failed
            record.errors.append(str(exc))
            self.store.put(record)
        finally:
            lease.release()

    def approve(self, run_id: str, recommendation_id: str, approved: bool) -> ApprovalResponse:
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

            command = make_resume_command(
                {
                    "recommendation_id": recommendation_id,
                    "decision": "approve" if approved else "reject",
                }
            )
            output = self._invoke_resume(run_id, command)
            updated = self._put_state(
                run_id,
                extract_state(output, graph=self.graph, config=self._graph_config(run_id)),
            )
            for item in updated.recommendations:
                if item.recommendation.recommendation_id == recommendation_id:
                    return ApprovalResponse(
                        run_id=run_id,
                        recommendation_id=recommendation_id,
                        status=item.status,
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


def compile_langgraph_probe() -> bool:
    """Return True when LangGraph is importable in the runtime image."""
    try:
        from langgraph.graph import END, START, StateGraph  # noqa: F401
    except Exception:
        return False
    return True
