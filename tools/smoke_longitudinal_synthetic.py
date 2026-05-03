from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent.cache import InMemoryRunLease  # noqa: E402
from src.agent.entities import (  # noqa: E402
    build_mission_state_summary,
    build_soldier_training_trajectory,
)
from src.agent.evaluation import evaluate_records, load_expected_fixture  # noqa: E402
from src.agent.graph import FallbackRangerGraph  # noqa: E402
from src.agent.store import InMemoryRunStore  # noqa: E402
from src.agent.workflow import RangerWorkflow  # noqa: E402
from src.config import Settings  # noqa: E402
from src.contracts import IngestEnvelope  # noqa: E402
from src.ingest.providers import (  # noqa: E402
    ProviderClients,
    heuristic_observations,
    heuristic_recommendations,
)

FIXTURE_PATH = ROOT / "assets/fixtures/envelopes/longitudinal_company_01.json"
EXPECTED_PATH = ROOT / "assets/fixtures/evals/longitudinal_company_01_expected.json"


class SyntheticKG:
    graph_name = "ranger"

    def __init__(self) -> None:
        self.observation_refs_by_soldier: dict[str, list[str]] = {}

    def health(self) -> bool:
        return True

    def write_observations(
        self, _ingest: IngestEnvelope, observations: list[Any]
    ) -> dict[str, int]:
        for observation in observations:
            self.observation_refs_by_soldier.setdefault(observation.soldier_id, []).append(
                f"falkor://ranger/Observation/{observation.observation_id}#history"
            )
        return {"observations": len(observations)}

    def write_recommendation(self, _recommendation: Any) -> None:
        return None

    def recent_observation_refs(self, soldier_ids: list[str]) -> dict[str, list[str]]:
        return {
            soldier_id: self.observation_refs_by_soldier.get(soldier_id, [])[-3:]
            for soldier_id in soldier_ids
        }


class SyntheticProviders:
    async def transcribe(self, _audio_b64: str) -> str:
        return ""

    async def ocr_pages(self, _image_b64: list[str]) -> list[Any]:
        return []

    async def extract_observations(self, text: str) -> list[Any]:
        return heuristic_observations(text)

    async def draft_recommendations(self, observations: list[Any]) -> list[Any]:
        return heuristic_recommendations(observations)


def main() -> int:
    args = _parse_args()
    envelopes = [
        IngestEnvelope.model_validate(item)
        for item in json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    ]
    if args.limit is not None:
        envelopes = envelopes[: args.limit]
    store = InMemoryRunStore()
    providers: SyntheticProviders | ProviderClients
    providers = (
        _openai_providers(args.openai_model, args.openai_extraction_model)
        if args.provider == "openai"
        else SyntheticProviders()
    )
    kg = SyntheticKG()
    workflow = RangerWorkflow(
        store=store,
        providers=cast(Any, providers),
        kg=cast(Any, kg),
        lease=InMemoryRunLease(),
        graph=FallbackRangerGraph(providers=cast(Any, providers), kg=cast(Any, kg)),
    )

    run_ids: list[str] = []
    for envelope in envelopes:
        record = workflow.create_run(envelope, trace_id=f"longitudinal-{envelope.envelope_id}")
        asyncio.run(workflow.process(record.run_id))
        _approve_all_pending(workflow, store, record.run_id)
        run_ids.append(record.run_id)

    mission_ids = sorted({envelope.mission_id for envelope in envelopes})
    soldier_ids = sorted(
        {
            observation.soldier_id
            for run_id in run_ids
            for observation in store.get(run_id).observations  # type: ignore[union-attr]
        }
    )
    mission_states: list[dict[str, Any]] = []
    for mission_id in mission_ids:
        mission_state = build_mission_state_summary(store, mission_id)
        if mission_state is None:
            raise RuntimeError(f"mission state for {mission_id} disappeared")
        mission_states.append(mission_state.model_dump(mode="json"))
    trajectories: list[dict[str, Any]] = []
    for soldier_id in soldier_ids:
        trajectory = build_soldier_training_trajectory(store, soldier_id)
        if trajectory is None:
            raise RuntimeError(f"trajectory for {soldier_id} disappeared")
        trajectories.append(trajectory.model_dump(mode="json"))
    llm_applied = _llm_applied_recommendations(store, run_ids)
    records = [_require_record(store, run_id) for run_id in run_ids]
    diagnostics = getattr(providers, "diagnostics", [])
    if args.evaluate:
        report = evaluate_records(
            records,
            load_expected_fixture(args.expected),
            provider_diagnostics=diagnostics,
            min_score=args.min_score,
            require_llm=args.require_llm,
            fail_on_fallback=args.fail_on_fallback,
        )
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0 if report.ok else 1

    if args.require_llm and not llm_applied:
        raise RuntimeError("OpenAI provider mode completed without model-applied recommendations")

    summary = {
        "ok": True,
        "fixture": str(FIXTURE_PATH.relative_to(ROOT)),
        "provider": args.provider,
        "openai_model": args.openai_model if args.provider == "openai" else None,
        "openai_extraction_model": args.openai_extraction_model
        if args.provider == "openai"
        else None,
        "run_count": len(run_ids),
        "platoons": sorted({envelope.platoon_id for envelope in envelopes}),
        "missions": mission_ids,
        "soldiers": soldier_ids,
        "mission_states": [_compact_mission_state(item) for item in mission_states],
        "soldier_trajectories": [_compact_trajectory(item) for item in trajectories],
        "llm_applied_recommendations": llm_applied,
        "provider_diagnostics": [item.model_dump(mode="json") for item in diagnostics],
        "update_events": len(store.list_update_events()),
        "outbox_events": len(
            [event for run_id in run_ids for event in store.list_outbox_events(run_id)]
        ),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the longitudinal multi-team fixture through the Ranger workflow."
    )
    parser.add_argument(
        "--provider",
        choices=("synthetic", "openai"),
        default="synthetic",
        help="Use deterministic heuristics or live OpenAI-backed extraction/ranking.",
    )
    parser.add_argument(
        "--openai-model",
        default=os.getenv("OPENAI_REASONING_MODEL", "gpt-5.5"),
        help="OpenAI reasoning model for --provider openai.",
    )
    parser.add_argument(
        "--openai-extraction-model",
        default=os.getenv(
            "OPENAI_EXTRACTION_MODEL", os.getenv("OPENAI_REASONING_MODEL", "gpt-5.5")
        ),
        help="OpenAI observation extraction model for --provider openai.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional number of fixture envelopes to process.",
    )
    parser.add_argument(
        "--require-llm",
        action="store_true",
        help="Fail if no recommendation carries an OpenAI model context ref.",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Print a structured evaluation report and exit nonzero when thresholds fail.",
    )
    parser.add_argument(
        "--expected",
        type=Path,
        default=EXPECTED_PATH,
        help="Expected-output fixture for --evaluate.",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.85,
        help="Minimum overall and non-hard metric score for --evaluate.",
    )
    parser.add_argument(
        "--fail-on-fallback",
        action="store_true",
        help="Fail --evaluate if provider diagnostics contain fallback or failed entries.",
    )
    return parser.parse_args()


def _openai_providers(model: str, extraction_model: str) -> ProviderClients:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for --provider openai")
    return ProviderClients(
        settings=Settings(
            openai_api_key=api_key,
            openai_extraction_model=extraction_model,
            openai_reasoning_model=model,
        )
    )


def _approve_all_pending(
    workflow: RangerWorkflow,
    store: InMemoryRunStore,
    run_id: str,
) -> None:
    while True:
        record = store.get(run_id)
        if record is None:
            raise RuntimeError(f"run {run_id} disappeared")
        pending = [item for item in record.recommendations if item.status == "pending"]
        if not pending:
            return
        item = pending[0]
        requirements = [
            requirement.requirement_id
            for requirement in item.recommendation.review_requirements
            if requirement.required_for_approval
        ]
        workflow.approve(
            run_id,
            item.recommendation.recommendation_id,
            approved=True,
            decision_rationale="Synthetic longitudinal smoke approval for trend capture.",
            acknowledged_review_requirements=requirements,
        )


def _llm_applied_recommendations(
    store: InMemoryRunStore, run_ids: list[str]
) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    for run_id in run_ids:
        record = store.get(run_id)
        if record is None:
            continue
        for item in record.recommendations:
            refs = [
                ref
                for ref in item.recommendation.model_context_refs
                if ref.startswith("model://openai/")
            ]
            if refs:
                applied.append(
                    {
                        "run_id": run_id,
                        "recommendation_id": item.recommendation.recommendation_id,
                        "target_soldier_id": item.recommendation.target_soldier_id,
                        "intervention_id": item.recommendation.intervention_id,
                        "status": item.status,
                        "model_context_refs": refs,
                        "decision_quality_rating": (
                            item.recommendation.decision_quality.rating
                            if item.recommendation.decision_quality
                            else None
                        ),
                    }
                )
    return applied


def _require_record(store: InMemoryRunStore, run_id: str) -> Any:
    record = store.get(run_id)
    if record is None:
        raise RuntimeError(f"run {run_id} disappeared")
    return record


def _compact_mission_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "mission_id": state["mission_id"],
        "platoon_id": state["platoon_id"],
        "phase": state["phase"],
        "mission_type": state["mission_type"],
        "status": state["status"],
        "soldier_ids": state["soldier_ids"],
        "total_observations": state["total_observations"],
        "approved_recommendations": state["approved_recommendations"],
        "platoon_readiness_score": state["platoon_readiness_score"],
    }


def _compact_trajectory(trajectory: dict[str, Any]) -> dict[str, Any]:
    return {
        "soldier_id": trajectory["soldier_id"],
        "run_count": trajectory["run_count"],
        "observation_count": trajectory["observation_count"],
        "approved_recommendation_count": trajectory["approved_recommendation_count"],
        "go_rate": trajectory["go_rate"],
        "readiness_score": trajectory["readiness_score"],
        "task_summaries": [
            {
                "task_code": item["task_code"],
                "go_count": item["go_count"],
                "nogo_count": item["nogo_count"],
                "uncertain_count": item["uncertain_count"],
                "latest_rating": item["latest_rating"],
                "trend": item["trend"],
            }
            for item in trajectory["task_summaries"]
        ],
        "development_edges": [
            {
                "development_edge": item["development_edge"],
                "approved_count": item["approved_count"],
            }
            for item in trajectory["development_edges"]
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
