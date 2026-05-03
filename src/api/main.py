from __future__ import annotations

from fastapi import BackgroundTasks, FastAPI, HTTPException

from src.agent.cache import redis_health
from src.agent.dashboard import build_dashboard_summary
from src.agent.store import build_run_store
from src.agent.vector_store import build_vector_store
from src.agent.workflow import RangerWorkflow, compile_langgraph_probe
from src.config import settings
from src.contracts import (
    ApprovalResponse,
    AuditEvent,
    DashboardRunSummary,
    IngestEnvelope,
    OutboxEvent,
    OutboxPublishResponse,
    RecommendationDecision,
    RunRecord,
)

store = build_run_store()
vector_store = build_vector_store()
workflow = RangerWorkflow(store=store)

app = FastAPI(
    title="C2D2 AVAI Ranger Agent",
    version="0.1.0",
    description="API-only deployable Ranger School adversarial training agent.",
)


@app.get("/v1/healthz")
def healthz() -> dict[str, object]:
    providers = {
        "anthropic": bool(settings.anthropic_api_key),
        "openai": bool(settings.openai_api_key),
        "deepgram": bool(settings.deepgram_api_key),
        "mistral": bool(settings.mistral_api_key),
        "openweather": bool(settings.openweather_api_key),
    }
    falkordb_available = workflow.kg.health()
    return {
        "ok": True,
        "langgraph_importable": compile_langgraph_probe(),
        "falkordb": falkordb_available,
        "dependencies_available": {
            "run_store": store.health(),
            "pgvector": vector_store.health() if vector_store is not None else False,
            "redis": redis_health(settings.redis_url),
            "falkordb": falkordb_available,
        },
        "providers_configured": providers,
        "openai_models": {
            "stt": settings.openai_stt_model,
            "multimodal": settings.openai_multimodal_model,
        },
        "infrastructure_configured": {
            "postgres": settings.postgres_configured,
            "pgvector": settings.postgres_configured,
            "redis": bool(settings.redis_url),
            "falkordb": bool(settings.falkordb_host),
        },
    }


@app.post("/v1/ingest", response_model=RunRecord, status_code=202)
async def ingest(envelope: IngestEnvelope, background_tasks: BackgroundTasks) -> RunRecord:
    record = workflow.create_run(envelope)
    background_tasks.add_task(workflow.process, record.run_id)
    return record


@app.get("/v1/runs/{run_id}", response_model=RunRecord)
def get_run(run_id: str) -> RunRecord:
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    return record


@app.get("/v1/dashboard/runs/{run_id}", response_model=DashboardRunSummary)
def get_dashboard_run(run_id: str) -> DashboardRunSummary:
    return build_dashboard_summary(get_run(run_id))


@app.get("/v1/runs/{run_id}/audit", response_model=list[AuditEvent])
def get_run_audit(run_id: str) -> list[AuditEvent]:
    get_run(run_id)
    return store.list_audit_events(run_id)


@app.get("/v1/outbox", response_model=list[OutboxEvent])
def list_pending_outbox_events(limit: int = 100) -> list[OutboxEvent]:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    return store.list_pending_outbox_events(limit=limit)


@app.post("/v1/outbox/{event_id}/published", response_model=OutboxPublishResponse)
def mark_outbox_event_published(event_id: str) -> OutboxPublishResponse:
    if not store.mark_outbox_event_published(event_id):
        raise HTTPException(status_code=404, detail="pending outbox event not found")
    return OutboxPublishResponse(event_id=event_id, status="published")


def _approve_recommendation(run_id: str, recommendation_id: str) -> ApprovalResponse:
    try:
        return workflow.approve(run_id, recommendation_id, approved=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _reject_recommendation(run_id: str, recommendation_id: str) -> ApprovalResponse:
    try:
        return workflow.approve(run_id, recommendation_id, approved=False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _approve_recommendation_by_id(recommendation_id: str) -> ApprovalResponse:
    run_id = _run_id_for_recommendation(recommendation_id)
    return _approve_recommendation(run_id, recommendation_id)


def _reject_recommendation_by_id(recommendation_id: str) -> ApprovalResponse:
    run_id = _run_id_for_recommendation(recommendation_id)
    return _reject_recommendation(run_id, recommendation_id)


@app.post("/v1/recommendations/{recommendation_id}/decision", response_model=ApprovalResponse)
def decide_recommendation(
    recommendation_id: str,
    decision: RecommendationDecision,
) -> ApprovalResponse:
    if decision.edited_recommendation is not None:
        raise HTTPException(
            status_code=501, detail="edited recommendations are not implemented yet"
        )
    if decision.decision == "approve":
        return _approve_recommendation_by_id(recommendation_id)
    return _reject_recommendation_by_id(recommendation_id)


def _run_id_for_recommendation(recommendation_id: str) -> str:
    run_id = store.find_run_id_for_recommendation(recommendation_id)
    if run_id is not None:
        return run_id
    raise HTTPException(status_code=404, detail="recommendation not found")
