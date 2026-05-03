from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    load_env_file(ROOT / ".env")
    load_env_file(ROOT / ".env.local")

    client = SmokeClient(
        base_url=os.getenv("SYSTEM1_BASE_URL", "http://127.0.0.1:8001").rstrip("/"),
        api_key=os.getenv("SYSTEM1_API_KEY"),
    )

    health = client.request("GET", "/v1/healthz")
    ready = client.request("GET", "/v1/readyz")
    accepted = client.request("POST", "/v1/ingest", _synthetic_ingest())
    run_id = str(accepted["run_id"])
    run = client.poll_run(run_id)
    if run["status"] == "failed":
        raise SmokeFailure(f"run failed: {run.get('errors')}")
    pending = next(item for item in run["recommendations"] if item["status"] == "pending")
    recommendation_id = pending["recommendation"]["recommendation_id"]

    dashboard = client.request("GET", f"/v1/dashboard/runs/{run_id}")
    mission_state = client.request("GET", "/v1/missions/m-smoke/state")
    decision = client.request(
        "POST",
        f"/v1/recommendations/{recommendation_id}/decision",
        {"decision": "approve"},
    )
    recommendation_detail = client.request("GET", f"/v1/recommendations/{recommendation_id}")
    detail = recommendation_detail["recommendation"]
    if not detail.get("evidence_summary"):
        raise SmokeFailure("recommendation detail missing evidence_summary")
    if not detail.get("model_context_refs"):
        raise SmokeFailure("recommendation detail missing model_context_refs")
    if not detail.get("score_breakdown"):
        raise SmokeFailure("recommendation detail missing score_breakdown")
    graph = client.request("GET", "/v1/graph/subgraph?mission_id=m-smoke")
    performance = client.request("GET", "/v1/soldiers/Jones/performance")
    trajectory = client.request("GET", "/v1/soldier/Jones/training-trajectory")
    lesson = client.request("POST", "/v1/lessons-learned", _synthetic_lesson(recommendation_id))
    duplicate_lesson = client.request(
        "POST",
        "/v1/lessons-learned",
        _synthetic_lesson(recommendation_id),
    )
    outbox = client.request("GET", "/v1/outbox")
    ledger = client.request("GET", f"/v1/update-ledger?entity_id={recommendation_id}")

    print(
        json.dumps(
            {
                "ok": True,
                "health_ok": health["ok"],
                "ready_ok": ready["ok"],
                "run_id": run_id,
                "run_status": client.request("GET", f"/v1/runs/{run_id}")["status"],
                "dashboard_pending_before_approval": dashboard["pending_recommendations"],
                "mission_state_observations": mission_state["total_observations"],
                "decision": decision["status"],
                "recommendation_detail_status": recommendation_detail["status"],
                "recommendation_has_evidence_summary": bool(detail.get("evidence_summary")),
                "recommendation_has_model_context_refs": bool(detail.get("model_context_refs")),
                "recommendation_has_score_breakdown": bool(detail.get("score_breakdown")),
                "graph_nodes": len(graph["nodes"]),
                "graph_edges": len(graph["edges"]),
                "approved_recommendations": len(performance["approved_recommendations"]),
                "trajectory_runs": trajectory["run_count"],
                "lesson_status": lesson["status"],
                "duplicate_lesson_status": duplicate_lesson["status"],
                "pending_outbox_events": len(outbox),
                "recommendation_ledger_events": len(ledger),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


class SmokeFailure(RuntimeError):
    pass


class SmokeClient:
    def __init__(self, base_url: str, api_key: str | None) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.trace_id = f"smoke-{int(time.time())}"

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode()
        headers = {
            "Accept": "application/json",
            "X-Trace-Id": self.trace_id,
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                data = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()
            raise SmokeFailure(f"{method} {path} returned {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise SmokeFailure(f"{method} {path} failed: {exc.reason}") from exc
        return json.loads(data.decode() or "{}")

    def poll_run(self, run_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + int(os.getenv("SMOKE_TIMEOUT_SECONDS", "30"))
        while time.monotonic() < deadline:
            run = self.request("GET", f"/v1/runs/{run_id}")
            if run["status"] in {"pending_approval", "completed", "failed"}:
                return run
            time.sleep(0.5)
        raise SmokeFailure(f"run {run_id} did not reach review state before timeout")


def _synthetic_ingest() -> dict[str, Any]:
    return {
        "instructor_id": "ri-smoke",
        "platoon_id": "plt-smoke",
        "mission_id": "m-smoke",
        "phase": "Mountain",
        "geo": {"lat": 35.0, "lon": -83.0, "grid_mgrs": "17S"},
        "free_text": (
            "Jones blew Phase Line Bird during movement. Smith asleep at 0300. "
            "Garcia textbook ambush rehearsal."
        ),
    }


def _synthetic_lesson(recommendation_id: str) -> dict[str, Any]:
    return {
        "lesson_id": "lesson-smoke-system1",
        "source_system": "system-3",
        "mission_id": "m-smoke",
        "soldier_ids": ["Jones"],
        "task_codes": ["MV-2"],
        "recommendation_ids": [recommendation_id],
        "summary": (
            "Synthetic smoke loop observed that compressed reporting improved "
            "follow-on coaching specificity."
        ),
        "evidence_refs": [
            {"ref": "system3://lessons/lesson-smoke-system1", "role": "source_lesson"}
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
