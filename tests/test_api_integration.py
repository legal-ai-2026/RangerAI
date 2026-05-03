from __future__ import annotations

import asyncio

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response

from src.agent.cache import InMemoryRunLease
from src.agent.graph import FallbackRangerGraph
from src.agent.store import InMemoryRunStore
from src.agent.workflow import RangerWorkflow
from src.api import main
from src.config import Settings
from src.contracts import GeoPoint, IngestEnvelope, Phase, RecommendationDecision
from src.ingest.providers import heuristic_observations, heuristic_recommendations


class FakeKG:
    graph_name = "ranger"

    def health(self) -> bool:
        return True

    def write_observations(self, _ingest, observations):
        return {"observations": len(observations)}

    def write_recommendation(self, _recommendation) -> None:
        return None


class FakeProviders:
    async def transcribe(self, _audio_b64: str) -> str:
        return ""

    async def ocr_pages(self, _image_b64: list[str]):
        return []

    async def extract_observations(self, text: str):
        return heuristic_observations(text)

    async def draft_recommendations(self, observations):
        return heuristic_recommendations(observations)


def _install_fake_runtime(monkeypatch, api_key: str | None = None) -> InMemoryRunStore:
    store = InMemoryRunStore()
    providers = FakeProviders()
    kg = FakeKG()
    workflow = RangerWorkflow(
        store=store,
        providers=providers,
        kg=kg,
        lease=InMemoryRunLease(),
        graph=FallbackRangerGraph(providers=providers, kg=kg),
    )
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "workflow", workflow)
    monkeypatch.setattr(main, "settings", Settings(system1_api_key=api_key))
    return store


async def _submit_ingest(envelope: IngestEnvelope):
    tasks = BackgroundTasks()
    record = await main.ingest(envelope, tasks)
    await tasks()
    return record


def _request(
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> Request:
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": raw_headers,
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


def test_http_ingest_review_decision_and_soldier_performance_flow(monkeypatch) -> None:
    _install_fake_runtime(monkeypatch)

    accepted = asyncio.run(
        _submit_ingest(
            IngestEnvelope(
                instructor_id="ri-1",
                platoon_id="plt-1",
                mission_id="m-1",
                phase=Phase.mountain,
                geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
                free_text=(
                    "Jones blew Phase Line Bird. Smith asleep at 0300. "
                    "Garcia textbook ambush rehearsal."
                ),
            )
        )
    )
    run_id = accepted.run_id

    run = main.get_run(run_id)
    assert run.status == "pending_approval"
    assert len(run.recommendations) == 3
    pending = next(item for item in run.recommendations if item.status == "pending")
    recommendation = pending.recommendation
    assert recommendation.intervention_id
    assert recommendation.score_breakdown.total

    dashboard = main.get_dashboard_run(run_id)
    assert dashboard.pending_recommendations == 3

    decision = main.decide_recommendation(
        recommendation.recommendation_id,
        RecommendationDecision(decision="approve"),
    )
    assert decision.status == "approved"

    performance = main.get_soldier_performance("Jones")
    assert performance.approved_recommendations
    assert not hasattr(performance.recent_observations[0], "note")


def test_configured_api_key_is_required_for_operational_v1_routes(monkeypatch) -> None:
    _install_fake_runtime(monkeypatch, api_key="dev-key")

    async def call_next(_request: Request) -> Response:
        return JSONResponse({"called": True}, status_code=209)

    health = asyncio.run(main.require_api_key(_request("/v1/healthz"), call_next))
    assert health.status_code == 209

    missing = asyncio.run(main.require_api_key(_request("/v1/runs/missing"), call_next))
    assert missing.status_code == 401

    authorized = asyncio.run(
        main.require_api_key(
            _request("/v1/runs/missing", headers={"X-API-Key": "dev-key"}),
            call_next,
        )
    )
    assert authorized.status_code == 209

    preflight = asyncio.run(
        main.require_api_key(
            _request("/v1/runs/missing", method="OPTIONS"),
            call_next,
        )
    )
    assert preflight.status_code == 209


def test_cors_allowlist_installs_expected_middleware() -> None:
    app = FastAPI()
    main._install_cors(app, Settings(cors_allow_origins=("http://localhost:3000",)))

    middleware = app.user_middleware[0]
    assert middleware.cls is CORSMiddleware
    assert middleware.kwargs["allow_origins"] == ["http://localhost:3000"]
    assert middleware.kwargs["allow_methods"] == ["GET", "POST", "OPTIONS"]
    assert "X-API-Key" in middleware.kwargs["allow_headers"]
