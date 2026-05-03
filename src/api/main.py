from __future__ import annotations

from collections.abc import Awaitable, Callable
import hashlib
import json
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import Response

from src.agent.cache import redis_health
from src.agent.dashboard import build_dashboard_summary
from src.agent.entities import (
    build_graph_subgraph,
    build_mission_entity_projection,
    build_mission_state_summary,
    build_soldier_entity_projection,
    build_soldier_performance_report,
    build_soldier_training_trajectory,
    get_recommendation_entity,
    list_recent_recommendation_entities,
)
from src.agent.store import build_run_store
from src.agent.vector_store import build_vector_store
from src.agent.workflow import RangerWorkflow, compile_langgraph_probe
from src.config import Settings, settings
from src.contracts import (
    ApprovalResponse,
    AuditEvent,
    DashboardRunSummary,
    DependencyStatus,
    EntityRecommendation,
    GraphSubgraph,
    IngestEnvelope,
    LessonsLearnedReceipt,
    LessonsLearnedSignal,
    MissionStateSummary,
    MissionEntityProjection,
    OutboxEvent,
    OutboxPublishResponse,
    RecommendationDecision,
    ReadinessReport,
    RunRecord,
    ScenarioRecommendation,
    SoldierEntityProjection,
    SoldierPerformanceReport,
    SoldierTrainingTrajectory,
    UpdateLedgerEntry,
)

store = build_run_store()
vector_store = build_vector_store()
workflow = RangerWorkflow(store=store)


def _install_cors(target_app: FastAPI, config: Settings) -> None:
    if not config.cors_allow_origins:
        return
    target_app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_allow_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Trace-Id"],
    )


app = FastAPI(
    title="C2D2 AVAI Ranger Agent",
    version="0.1.0",
    description="API-only deployable Ranger School adversarial training agent.",
)

_install_cors(app, settings)


@app.middleware("http")
async def attach_trace_id(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    trace_id = _trace_id(request)
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.middleware("http")
async def require_api_key(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if _requires_api_key(request):
        provided = request.headers.get("x-api-key")
        if provided != settings.system1_api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "invalid or missing API key"},
                headers={"X-Trace-Id": _trace_id(request)},
            )
    return await call_next(request)


@app.get("/v1/healthz")
def healthz() -> dict[str, object]:
    providers = _provider_status()
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


@app.get("/v1/readyz", response_model=ReadinessReport)
def readyz(response: Response) -> ReadinessReport:
    report = _readiness_report()
    if not report.ok:
        response.status_code = 503
    return report


@app.post("/v1/ingest", response_model=RunRecord, status_code=202)
async def ingest(
    envelope: IngestEnvelope,
    background_tasks: BackgroundTasks,
    request: Request,
) -> RunRecord:
    trace_id = _trace_id(request)
    record = workflow.create_run(envelope, trace_id=trace_id)
    background_tasks.add_task(workflow.process, record.run_id, trace_id)
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


@app.get("/v1/missions/{mission_id}/state", response_model=MissionStateSummary)
def get_mission_state(
    mission_id: str,
    limit: int = 100,
) -> MissionStateSummary:
    _validate_lookup_limit(limit)
    summary = build_mission_state_summary(store, mission_id, limit=limit)
    if summary is None:
        raise HTTPException(status_code=404, detail="mission state not found")
    return summary


@app.get("/v1/entities/soldiers/{soldier_id}", response_model=SoldierEntityProjection)
def get_soldier_entity(
    soldier_id: str,
    limit: int = 100,
) -> SoldierEntityProjection:
    _validate_lookup_limit(limit)
    projection = build_soldier_entity_projection(store, soldier_id, limit=limit)
    if projection is None:
        raise HTTPException(status_code=404, detail="soldier projection not found")
    return projection


@app.get("/v1/entities/missions/{mission_id}", response_model=MissionEntityProjection)
def get_mission_entity(
    mission_id: str,
    limit: int = 100,
) -> MissionEntityProjection:
    _validate_lookup_limit(limit)
    projection = build_mission_entity_projection(store, mission_id, limit=limit)
    if projection is None:
        raise HTTPException(status_code=404, detail="mission projection not found")
    return projection


@app.get("/v1/soldiers/{soldier_id}/performance", response_model=SoldierPerformanceReport)
def get_soldier_performance(
    soldier_id: str,
    limit: int = 100,
) -> SoldierPerformanceReport:
    _validate_lookup_limit(limit)
    report = build_soldier_performance_report(store, soldier_id, limit=limit)
    if report is None:
        raise HTTPException(status_code=404, detail="soldier performance not found")
    return report


@app.get("/v1/soldier/{soldier_id}/training-trajectory", response_model=SoldierTrainingTrajectory)
def get_soldier_training_trajectory(
    soldier_id: str,
    limit: int = 100,
) -> SoldierTrainingTrajectory:
    _validate_lookup_limit(limit)
    trajectory = build_soldier_training_trajectory(store, soldier_id, limit=limit)
    if trajectory is None:
        raise HTTPException(status_code=404, detail="soldier training trajectory not found")
    return trajectory


@app.get("/v1/runs/{run_id}/audit", response_model=list[AuditEvent])
def get_run_audit(run_id: str) -> list[AuditEvent]:
    get_run(run_id)
    return store.list_audit_events(run_id)


@app.get("/v1/recommendations/recent", response_model=list[EntityRecommendation])
def list_recent_recommendations(
    mission_id: str | None = None,
    status: str | None = None,
    limit: int = 25,
) -> list[EntityRecommendation]:
    _validate_lookup_limit(limit)
    if status is not None and status not in {"pending", "approved", "rejected", "blocked"}:
        raise HTTPException(status_code=422, detail="status is not a recommendation status")
    return list_recent_recommendation_entities(
        store,
        mission_id=mission_id,
        status=status,
        limit=limit,
    )


@app.get("/v1/recommendations/{recommendation_id}", response_model=EntityRecommendation)
def get_recommendation(recommendation_id: str) -> EntityRecommendation:
    recommendation = get_recommendation_entity(store, recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return recommendation


@app.get("/v1/graph/subgraph", response_model=GraphSubgraph)
def get_graph_subgraph(
    run_id: str | None = None,
    mission_id: str | None = None,
    soldier_id: str | None = None,
    limit: int = 100,
) -> GraphSubgraph:
    _validate_lookup_limit(limit)
    subgraph = build_graph_subgraph(
        store,
        run_id=run_id,
        mission_id=mission_id,
        soldier_id=soldier_id,
        limit=limit,
    )
    if subgraph is None:
        raise HTTPException(status_code=404, detail="graph subgraph not found")
    return subgraph


@app.get("/v1/outbox", response_model=list[OutboxEvent])
def list_pending_outbox_events(limit: int = 100) -> list[OutboxEvent]:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    return store.list_pending_outbox_events(limit=limit)


@app.get("/v1/update-ledger", response_model=list[UpdateLedgerEntry])
def list_update_ledger(
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = 100,
) -> list[UpdateLedgerEntry]:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    return store.list_update_events(entity_type=entity_type, entity_id=entity_id, limit=limit)


@app.post("/v1/outbox/{event_id}/published", response_model=OutboxPublishResponse)
def mark_outbox_event_published(event_id: str) -> OutboxPublishResponse:
    if not store.mark_outbox_event_published(event_id):
        raise HTTPException(status_code=404, detail="pending outbox event not found")
    return OutboxPublishResponse(event_id=event_id, status="published")


@app.post("/v1/lessons-learned", response_model=LessonsLearnedReceipt, status_code=202)
def record_lessons_learned(
    lesson: LessonsLearnedSignal,
    request: Request,
) -> LessonsLearnedReceipt:
    trace_id = _trace_id(request)
    inserted = store.put_lesson_signal(lesson)
    source_refs = _lesson_source_refs(lesson)
    if inserted:
        payload = lesson.model_dump(mode="json")
        store.append_update_event(
            UpdateLedgerEntry(
                entity_type="lesson_signal",
                entity_id=lesson.lesson_id,
                source_service=lesson.source_system,
                operation="create",
                trace_id=trace_id,
                patch=payload,
                source_refs=source_refs,
                content_hash_after=_content_hash(payload),
            )
        )
    return LessonsLearnedReceipt(
        lesson_id=lesson.lesson_id,
        status="accepted" if inserted else "duplicate",
        source_refs=source_refs,
    )


def _approve_recommendation(
    run_id: str,
    recommendation_id: str,
    edited_recommendation: ScenarioRecommendation | None = None,
    trace_id: str | None = None,
) -> ApprovalResponse:
    try:
        return workflow.approve(
            run_id,
            recommendation_id,
            approved=True,
            edited_recommendation=edited_recommendation,
            trace_id=trace_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _reject_recommendation(
    run_id: str,
    recommendation_id: str,
    trace_id: str | None = None,
) -> ApprovalResponse:
    try:
        return workflow.approve(
            run_id,
            recommendation_id,
            approved=False,
            trace_id=trace_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _approve_recommendation_by_id(
    recommendation_id: str,
    edited_recommendation: ScenarioRecommendation | None = None,
    trace_id: str | None = None,
) -> ApprovalResponse:
    run_id = _run_id_for_recommendation(recommendation_id)
    return _approve_recommendation(run_id, recommendation_id, edited_recommendation, trace_id)


def _reject_recommendation_by_id(
    recommendation_id: str,
    trace_id: str | None = None,
) -> ApprovalResponse:
    run_id = _run_id_for_recommendation(recommendation_id)
    return _reject_recommendation(run_id, recommendation_id, trace_id)


@app.post("/v1/recommendations/{recommendation_id}/decision", response_model=ApprovalResponse)
def decide_recommendation(
    recommendation_id: str,
    decision: RecommendationDecision,
    request: Request,
) -> ApprovalResponse:
    trace_id = _trace_id(request)
    if decision.decision == "approve":
        return _approve_recommendation_by_id(
            recommendation_id,
            decision.edited_recommendation,
            trace_id,
        )
    return _reject_recommendation_by_id(recommendation_id, trace_id)


def _run_id_for_recommendation(recommendation_id: str) -> str:
    run_id = store.find_run_id_for_recommendation(recommendation_id)
    if run_id is not None:
        return run_id
    raise HTTPException(status_code=404, detail="recommendation not found")


def _validate_lookup_limit(limit: int) -> None:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")


def _requires_api_key(request: Request) -> bool:
    if not settings.system1_api_key:
        return False
    if request.method == "OPTIONS":
        return False
    return request.url.path.startswith("/v1/") and request.url.path not in {
        "/v1/healthz",
        "/v1/readyz",
    }


def _trace_id(request: Request) -> str:
    existing = getattr(request.state, "trace_id", None)
    if existing:
        return str(existing)
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    request.state.trace_id = trace_id
    return trace_id


def _provider_status() -> dict[str, bool]:
    return {
        "anthropic": bool(settings.anthropic_api_key),
        "openai": bool(settings.openai_api_key),
        "deepgram": bool(settings.deepgram_api_key),
        "mistral": bool(settings.mistral_api_key),
        "openweather": bool(settings.openweather_api_key),
    }


def _readiness_report() -> ReadinessReport:
    dependencies = [
        DependencyStatus(name="run_store", ok=store.health(), critical=True),
        DependencyStatus(
            name="pgvector",
            ok=vector_store.health() if vector_store is not None else False,
            critical=False,
        ),
        DependencyStatus(name="redis", ok=redis_health(settings.redis_url), critical=False),
        DependencyStatus(name="falkordb", ok=workflow.kg.health(), critical=True),
        DependencyStatus(name="langgraph", ok=compile_langgraph_probe(), critical=True),
    ]
    return ReadinessReport(
        ok=all(item.ok for item in dependencies if item.critical),
        dependencies=dependencies,
        providers_configured=_provider_status(),
        openai_models={
            "stt": settings.openai_stt_model,
            "multimodal": settings.openai_multimodal_model,
        },
    )


def _lesson_source_refs(lesson: LessonsLearnedSignal) -> list[str]:
    refs = [f"postgres://ranger_lesson_signals/{lesson.lesson_id}"]
    refs.extend(ref.ref for ref in lesson.evidence_refs)
    refs.extend(
        f"postgres://ranger_runs/{run_id}#record.recommendations"
        for run_id in _run_ids_for_recommendations(lesson.recommendation_ids)
    )
    return sorted(set(refs))


def _run_ids_for_recommendations(recommendation_ids: list[str]) -> list[str]:
    run_ids: list[str] = []
    for recommendation_id in recommendation_ids:
        run_id = store.find_run_id_for_recommendation(recommendation_id)
        if run_id is not None:
            run_ids.append(run_id)
    return run_ids


def _content_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
