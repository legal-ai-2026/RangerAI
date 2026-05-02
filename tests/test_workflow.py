import asyncio

from src.agent.store import InMemoryRunStore
from src.agent.workflow import RangerWorkflow
from src.api import main
from src.contracts import GeoPoint, IngestEnvelope, Phase
from src.ingest.providers import heuristic_observations, heuristic_recommendations


class FakeKG:
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


def test_ingest_to_approval_workflow() -> None:
    store = InMemoryRunStore()
    workflow = RangerWorkflow(store=store, providers=FakeProviders(), kg=FakeKG())
    record = workflow.create_run(
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

    asyncio.run(workflow.process(record.run_id))
    completed = store.get(record.run_id)
    assert completed is not None
    assert completed.status == "pending_approval"
    assert len(completed.observations) == 3
    assert len(completed.recommendations) == 3

    pending = next(item for item in completed.recommendations if item.status == "pending")
    approval = workflow.approve(
        completed.run_id,
        pending.recommendation.recommendation_id,
        approved=True,
    )
    assert approval.status == "approved"


def test_dashboard_summary_includes_soldier_metrics_and_recommendations() -> None:
    store = InMemoryRunStore()
    workflow = RangerWorkflow(store=store, providers=FakeProviders(), kg=FakeKG())
    record = workflow.create_run(
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
    asyncio.run(workflow.process(record.run_id))
    completed = store.get(record.run_id)
    assert completed is not None

    summary = main.build_dashboard_summary(completed)
    assert summary.total_observations == 3
    assert summary.pending_recommendations == 3
    assert summary.platoon_readiness_score > 0
    assert {soldier.soldier_id for soldier in summary.soldiers} == {"Garcia", "Jones", "Smith"}
    jones = next(soldier for soldier in summary.soldiers if soldier.soldier_id == "Jones")
    assert jones.metrics
    assert jones.active_recommendations


def test_v1_decision_rejects_pending_recommendation() -> None:
    store = InMemoryRunStore()
    workflow = RangerWorkflow(store=store, providers=FakeProviders(), kg=FakeKG())
    previous_store = main.store
    previous_workflow = main.workflow
    try:
        main.store = store
        main.workflow = workflow
        record = workflow.create_run(
            IngestEnvelope(
                instructor_id="ri-1",
                platoon_id="plt-1",
                mission_id="m-1",
                phase=Phase.mountain,
                geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
                free_text="Jones blew Phase Line Bird.",
            )
        )
        asyncio.run(workflow.process(record.run_id))
        completed = store.get(record.run_id)
        assert completed is not None
        pending = next(item for item in completed.recommendations if item.status == "pending")

        response = main.decide_recommendation(
            pending.recommendation.recommendation_id,
            main.RecommendationDecision(decision="reject"),
        )
        assert response.status == "rejected"
    finally:
        main.store = previous_store
        main.workflow = previous_workflow
