from __future__ import annotations

from fastapi import BackgroundTasks, FastAPI, HTTPException

from src.agent.dashboard import build_dashboard_summary
from src.agent.store import InMemoryRunStore
from src.agent.workflow import RangerWorkflow, compile_langgraph_probe
from src.config import settings
from src.contracts import (
    ApprovalResponse,
    DashboardRunSummary,
    IngestEnvelope,
    RecommendationDecision,
    RunRecord,
)

store = InMemoryRunStore()
workflow = RangerWorkflow(store=store)

app = FastAPI(
    title="C2D2 AVAI Ranger Agent",
    version="0.1.0",
    description="API-only deployable Ranger School adversarial training agent.",
)


@app.get("/healthz")
def healthz() -> dict[str, object]:
    providers = {
        "anthropic": bool(settings.anthropic_api_key),
        "openai": bool(settings.openai_api_key),
        "deepgram": bool(settings.deepgram_api_key),
        "mistral": bool(settings.mistral_api_key),
        "openweather": bool(settings.openweather_api_key),
    }
    return {
        "ok": True,
        "langgraph_importable": compile_langgraph_probe(),
        "falkordb": workflow.kg.health(),
        "providers_configured": providers,
        "openai_models": {
            "stt": settings.openai_stt_model,
            "multimodal": settings.openai_multimodal_model,
        },
    }


@app.get("/v1/healthz")
def v1_healthz() -> dict[str, object]:
    return healthz()


@app.post("/ingest", response_model=RunRecord, status_code=202)
async def ingest(envelope: IngestEnvelope, background_tasks: BackgroundTasks) -> RunRecord:
    record = workflow.create_run(envelope)
    background_tasks.add_task(workflow.process, record.run_id)
    return record


@app.post("/v1/ingest", response_model=RunRecord, status_code=202)
async def v1_ingest(envelope: IngestEnvelope, background_tasks: BackgroundTasks) -> RunRecord:
    return await ingest(envelope, background_tasks)


@app.get("/runs/{run_id}", response_model=RunRecord)
def get_run(run_id: str) -> RunRecord:
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    return record


@app.get("/v1/runs/{run_id}", response_model=RunRecord)
def v1_get_run(run_id: str) -> RunRecord:
    return get_run(run_id)


@app.get("/v1/dashboard/runs/{run_id}", response_model=DashboardRunSummary)
def get_dashboard_run(run_id: str) -> DashboardRunSummary:
    return build_dashboard_summary(get_run(run_id))


@app.post("/recommendations/{run_id}/{recommendation_id}/approve", response_model=ApprovalResponse)
def approve_recommendation(run_id: str, recommendation_id: str) -> ApprovalResponse:
    try:
        return workflow.approve(run_id, recommendation_id, approved=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/recommendations/{run_id}/{recommendation_id}/reject", response_model=ApprovalResponse)
def reject_recommendation(run_id: str, recommendation_id: str) -> ApprovalResponse:
    try:
        return workflow.approve(run_id, recommendation_id, approved=False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/recommendations/{recommendation_id}/approve", response_model=ApprovalResponse)
def approve_recommendation_by_id(recommendation_id: str) -> ApprovalResponse:
    run_id = _run_id_for_recommendation(recommendation_id)
    return approve_recommendation(run_id, recommendation_id)


@app.post("/recommendations/{recommendation_id}/reject", response_model=ApprovalResponse)
def reject_recommendation_by_id(recommendation_id: str) -> ApprovalResponse:
    run_id = _run_id_for_recommendation(recommendation_id)
    return reject_recommendation(run_id, recommendation_id)


@app.post("/v1/recommendations/{recommendation_id}/decision", response_model=ApprovalResponse)
def decide_recommendation(
    recommendation_id: str,
    decision: RecommendationDecision,
) -> ApprovalResponse:
    if decision.edited_recommendation is not None:
        raise HTTPException(status_code=501, detail="edited recommendations are not implemented yet")
    if decision.decision == "approve":
        return approve_recommendation_by_id(recommendation_id)
    return reject_recommendation_by_id(recommendation_id)


def _run_id_for_recommendation(recommendation_id: str) -> str:
    for record in store.records.values():
        for item in record.recommendations:
            if item.recommendation.recommendation_id == recommendation_id:
                return record.run_id
    raise HTTPException(status_code=404, detail="recommendation not found")
