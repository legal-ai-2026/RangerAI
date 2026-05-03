import asyncio
import json
from pathlib import Path
from typing import Any, cast

from src.agent.cache import InMemoryRunLease
from src.agent.entities import (
    build_mission_state_summary,
    build_soldier_training_trajectory,
)
from src.agent.evaluation import evaluate_records, load_expected_fixture
from src.agent.graph import FallbackRangerGraph
from src.agent.store import InMemoryRunStore
from src.agent.workflow import RangerWorkflow
from src.contracts import IngestEnvelope
from src.ingest.providers import heuristic_observations, heuristic_recommendations

FIXTURE_PATH = Path("assets/fixtures/envelopes/longitudinal_company_01.json")
EXPECTED_PATH = Path("assets/fixtures/evals/longitudinal_company_01_expected.json")


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


def test_longitudinal_fixture_captures_multi_team_mission_trajectories() -> None:
    store = InMemoryRunStore()
    providers = SyntheticProviders()
    kg = SyntheticKG()
    workflow = RangerWorkflow(
        store=store,
        providers=cast(Any, providers),
        kg=cast(Any, kg),
        lease=InMemoryRunLease(),
        graph=FallbackRangerGraph(providers=cast(Any, providers), kg=cast(Any, kg)),
    )
    envelopes = [
        IngestEnvelope.model_validate(item)
        for item in json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    ]

    run_ids: list[str] = []
    for envelope in envelopes:
        record = workflow.create_run(envelope, trace_id=f"test-{envelope.envelope_id}")
        asyncio.run(workflow.process(record.run_id))
        _approve_all_pending(workflow, store, record.run_id)
        run_ids.append(record.run_id)

    assert len(run_ids) == 8
    assert {envelope.platoon_id for envelope in envelopes} == {"plt-alpha", "plt-bravo"}
    assert all(_require_record(store, run_id).status == "completed" for run_id in run_ids)

    jones = build_soldier_training_trajectory(store, "Jones")
    taylor = build_soldier_training_trajectory(store, "Taylor")
    assert jones is not None
    assert taylor is not None
    assert jones.run_count == 4
    assert taylor.run_count == 4
    assert jones.approved_recommendation_count >= 1
    assert taylor.approved_recommendation_count >= 1
    assert _task_trend(jones, "MV-2") == "improving"
    assert _task_trend(taylor, "MV-2") == "improving"

    alpha_missions = store.list_runs_for_mission("m-alpha-mountain-amb-03")
    bravo_missions = store.list_runs_for_mission("m-bravo-florida-amb-03")
    assert alpha_missions[0].ingest.platoon_id == "plt-alpha"
    assert bravo_missions[0].ingest.platoon_id == "plt-bravo"

    alpha_state = build_mission_state_summary(store, "m-alpha-mountain-amb-03")
    bravo_state = build_mission_state_summary(store, "m-bravo-florida-amb-03")
    assert alpha_state is not None
    assert bravo_state is not None
    assert alpha_state.total_observations == 3
    assert bravo_state.total_observations == 3
    assert alpha_state.approved_recommendations >= 1
    assert bravo_state.approved_recommendations >= 1
    assert store.list_update_events(entity_type="observation")
    assert store.list_update_events(entity_type="recommendation")

    report = evaluate_records(
        [_require_record(store, run_id) for run_id in run_ids],
        load_expected_fixture(EXPECTED_PATH),
    )
    assert report.ok
    assert report.overall_score == 1.0


def _approve_all_pending(
    workflow: RangerWorkflow,
    store: InMemoryRunStore,
    run_id: str,
) -> None:
    while True:
        record = store.get(run_id)
        assert record is not None
        pending = [item for item in record.recommendations if item.status == "pending"]
        if not pending:
            return
        recommendation = pending[0].recommendation
        requirements = [
            item.requirement_id
            for item in recommendation.review_requirements
            if item.required_for_approval
        ]
        workflow.approve(
            run_id,
            recommendation.recommendation_id,
            approved=True,
            decision_rationale="Synthetic longitudinal test approval.",
            acknowledged_review_requirements=requirements,
        )


def _task_trend(trajectory: Any, task_code: str) -> str:
    summary = next(item for item in trajectory.task_summaries if item.task_code == task_code)
    return str(summary.trend)


def _require_record(store: InMemoryRunStore, run_id: str) -> Any:
    record = store.get(run_id)
    assert record is not None
    return record
