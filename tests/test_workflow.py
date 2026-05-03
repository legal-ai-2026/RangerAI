import asyncio
from enum import Enum
from typing import Any, cast

import pytest
from fastapi import Request
from pydantic import BaseModel

from src.agent.decision_science import MEDIUM_RISK_ACK
from src.agent.cache import InMemoryRunLease
from src.agent.graph import (
    FallbackRangerGraph,
    build_ranger_graph,
    extract_state,
    to_checkpoint_state,
)
from src.agent.store import InMemoryRunStore
from src.agent.workflow import RangerWorkflow
from src.api import main
from src.contracts import GeoPoint, IngestEnvelope, ORBookletPage, ORBookletRow, Phase
from src.ingest.providers import heuristic_observations, heuristic_recommendations


class FakeKG:
    graph_name = "ranger"

    def health(self) -> bool:
        return True

    def write_observations(self, _ingest, observations):
        return {"observations": len(observations)}

    def write_recommendation(self, _recommendation) -> None:
        return None

    def recent_observation_refs(self, soldier_ids):
        return {soldier_id: [] for soldier_id in soldier_ids}


class HistoryKG(FakeKG):
    def recent_observation_refs(self, soldier_ids):
        return {
            soldier_id: [f"falkor://ranger/Observation/history-{soldier_id}#history"]
            for soldier_id in soldier_ids
        }


class FailingKG(FakeKG):
    def write_observations(self, _ingest, _observations):
        raise RuntimeError("FalkorDB unavailable")


class FakeProviders:
    async def transcribe(self, _audio_b64: str) -> str:
        return ""

    async def ocr_pages(self, _image_b64: list[str]):
        return []

    async def extract_observations(self, text: str):
        return heuristic_observations(text)

    async def draft_recommendations(self, observations):
        return heuristic_recommendations(observations)


class ImageProviders(FakeProviders):
    async def ocr_pages(self, _image_b64: list[str]):
        return [
            ORBookletPage(
                confidence=0.92,
                rows=[
                    ORBookletRow(
                        task_code="MV-2",
                        task_name="Movement report",
                        rating="NOGO",
                        observation_note="Jones missed Phase Line Bird and did not send a SITREP.",
                    )
                ],
            )
        ]


class LowConfidenceImageProviders(FakeProviders):
    async def ocr_pages(self, _image_b64: list[str]):
        return [
            ORBookletPage(
                confidence=0.42,
                rows=[
                    ORBookletRow(
                        task_code="MV-2",
                        task_name="Movement report",
                        rating="NOGO",
                        observation_note="Jones maybe missed Phase Line Bird.",
                    )
                ],
            )
        ]


def fake_workflow(
    store: InMemoryRunStore,
    providers: FakeProviders | None = None,
    kg: FakeKG | None = None,
    lease: InMemoryRunLease | None = None,
) -> RangerWorkflow:
    providers = providers or FakeProviders()
    kg = kg or FakeKG()
    return RangerWorkflow(
        store=store,
        providers=cast(Any, providers),
        kg=cast(Any, kg),
        lease=lease,
        graph=FallbackRangerGraph(providers=cast(Any, providers), kg=cast(Any, kg)),
    )


def _request(path: str, method: str = "POST") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


def test_ingest_to_approval_workflow() -> None:
    store = InMemoryRunStore()
    workflow = fake_workflow(store)
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
        ),
        trace_id="trace-workflow",
    )

    asyncio.run(workflow.process(record.run_id))
    completed = store.get(record.run_id)
    assert completed is not None
    assert completed.status == "pending_approval"
    assert len(completed.observations) == 3
    assert len(completed.recommendations) == 3
    first_recommendation = completed.recommendations[0].recommendation
    assert first_recommendation.target_ids.mission_id == "m-1"
    assert first_recommendation.target_ids.platoon_id == "plt-1"
    assert first_recommendation.evidence_refs
    assert f"postgres://ranger_runs/{record.run_id}#record.observations" in (
        first_recommendation.model_context_refs
    )
    assert any(
        ref.startswith("asset://doctrine/") for ref in first_recommendation.model_context_refs
    )
    assert any(
        ref.startswith("synthetic://weather/") for ref in first_recommendation.model_context_refs
    )
    assert first_recommendation.decision_frame is not None
    assert first_recommendation.decision_quality is not None
    assert first_recommendation.value_of_information is not None

    pending = next(item for item in completed.recommendations if item.status == "pending")
    approval = workflow.approve(
        completed.run_id,
        pending.recommendation.recommendation_id,
        approved=True,
    )
    assert approval.status == "approved"
    audit_event_types = {event.event_type for event in store.list_audit_events(record.run_id)}
    assert {
        "run_accepted",
        "run_processing_started",
        "run_status_updated",
        "recommendation_decision_recorded",
    }.issubset(audit_event_types)
    outbox_events = store.list_outbox_events(record.run_id)
    assert [event.event_type for event in outbox_events] == ["recommendation.approved"]
    assert outbox_events[0].trace_id == "trace-workflow"
    assert outbox_events[0].payload["target_ids"] == pending.recommendation.target_ids.model_dump(
        mode="json", exclude_none=True
    )
    assert outbox_events[0].payload["evidence_refs"]
    update_events = store.list_update_events()
    assert len([event for event in update_events if event.entity_type == "observation"]) == 3
    recommendation_updates = [
        event for event in update_events if event.entity_type == "recommendation"
    ]
    assert len(recommendation_updates) == 1
    assert recommendation_updates[0].operation == "approve"
    assert recommendation_updates[0].trace_id == "trace-workflow"
    assert recommendation_updates[0].source_refs
    assert recommendation_updates[0].patch["decision_quality"]


def test_graph_checkpoint_state_is_json_safe() -> None:
    providers = FakeProviders()
    kg = FakeKG()
    graph = build_ranger_graph(providers=cast(Any, providers), kg=cast(Any, kg))
    state = {
        "run_id": "run-json-safe",
        "ingest": IngestEnvelope(
            instructor_id="ri-1",
            platoon_id="plt-1",
            mission_id="m-1",
            phase=Phase.mountain,
            geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
            free_text="Jones blew Phase Line Bird.",
        ),
    }

    output = asyncio.run(
        graph.ainvoke(
            to_checkpoint_state(state),
            config={"configurable": {"thread_id": "run-json-safe"}},
        )
    )
    if "__interrupt__" in output:
        snapshot = graph.get_state({"configurable": {"thread_id": "run-json-safe"}})
        _assert_json_safe(snapshot.values)
        typed = extract_state(
            output,
            graph=graph,
            config={"configurable": {"thread_id": "run-json-safe"}},
        )
        assert typed["status"] == "pending_approval"
    else:
        _assert_json_safe(output)


def _assert_json_safe(value: Any) -> None:
    assert not isinstance(value, BaseModel)
    assert not isinstance(value, Enum)
    if isinstance(value, dict):
        for item in value.values():
            _assert_json_safe(item)
    elif isinstance(value, list):
        for item in value:
            _assert_json_safe(item)


def test_medium_risk_approval_requires_rationale_and_acknowledgement() -> None:
    store = InMemoryRunStore()
    workflow = fake_workflow(store)
    record = workflow.create_run(
        IngestEnvelope(
            instructor_id="ri-1",
            platoon_id="plt-1",
            mission_id="m-1",
            phase=Phase.mountain,
            geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
            free_text="Smith asleep at 0300 during patrol-base security.",
        )
    )
    asyncio.run(workflow.process(record.run_id))
    completed = store.get(record.run_id)
    assert completed is not None
    pending = next(item for item in completed.recommendations if item.status == "pending")
    recommendation = pending.recommendation
    assert MEDIUM_RISK_ACK in {
        requirement.requirement_id for requirement in recommendation.review_requirements
    }

    with pytest.raises(ValueError, match="acknowledged review requirements"):
        workflow.approve(completed.run_id, recommendation.recommendation_id, approved=True)

    with pytest.raises(ValueError, match="decision_rationale"):
        workflow.approve(
            completed.run_id,
            recommendation.recommendation_id,
            approved=True,
            acknowledged_review_requirements=[MEDIUM_RISK_ACK],
        )

    approval = workflow.approve(
        completed.run_id,
        recommendation.recommendation_id,
        approved=True,
        decision_rationale="Instructor reviewed fatigue controls and accepts the supervised inject.",
        acknowledged_review_requirements=[MEDIUM_RISK_ACK],
    )

    assert approval.status == "approved"
    audit_event = next(
        event
        for event in store.list_audit_events(record.run_id)
        if event.recommendation_id == recommendation.recommendation_id
    )
    assert audit_event.payload["decision_rationale"]
    assert audit_event.payload["acknowledged_review_requirements"] == [MEDIUM_RISK_ACK]


def test_recommendation_context_includes_recent_kg_history() -> None:
    store = InMemoryRunStore()
    workflow = fake_workflow(store, kg=HistoryKG())
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
    recommendation = completed.recommendations[0].recommendation
    assert "falkor://ranger/Observation/history-Jones#history" in (
        recommendation.model_context_refs
    )


def test_process_continues_when_kg_write_fails() -> None:
    store = InMemoryRunStore()
    workflow = fake_workflow(store, kg=FailingKG())
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
    assert completed.status == "pending_approval"
    assert completed.recommendations
    assert completed.kg_write_summary == {"observations": 0}
    assert completed.errors == ["KG write failed: FalkorDB unavailable"]


def test_image_only_ingest_uses_ocr_rows_for_observations_and_recommendations() -> None:
    store = InMemoryRunStore()
    workflow = fake_workflow(store, providers=ImageProviders())
    record = workflow.create_run(
        IngestEnvelope(
            instructor_id="ri-1",
            platoon_id="plt-1",
            mission_id="m-1",
            phase=Phase.mountain,
            geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
            image_b64=["fake-image"],
        )
    )

    asyncio.run(workflow.process(record.run_id))

    completed = store.get(record.run_id)
    assert completed is not None
    assert completed.observations[0].source == "image"
    assert completed.observations[0].rating == "NOGO"
    assert completed.recommendations
    recommendation = completed.recommendations[0].recommendation
    assert recommendation.evidence_summary
    assert recommendation.score_breakdown is not None
    assert recommendation.model_context_refs


def test_low_confidence_ocr_rows_remain_uncertain_and_do_not_drive_recommendations() -> None:
    store = InMemoryRunStore()
    workflow = fake_workflow(store, providers=LowConfidenceImageProviders())
    record = workflow.create_run(
        IngestEnvelope(
            instructor_id="ri-1",
            platoon_id="plt-1",
            mission_id="m-1",
            phase=Phase.mountain,
            geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
            image_b64=["fake-image"],
        )
    )

    asyncio.run(workflow.process(record.run_id))

    completed = store.get(record.run_id)
    assert completed is not None
    assert completed.observations[0].rating == "UNCERTAIN"
    assert completed.observations[0].uncertainty_refs
    assert completed.recommendations == []
    assert completed.status == "completed"


def test_dashboard_summary_includes_soldier_metrics_and_recommendations() -> None:
    store = InMemoryRunStore()
    workflow = fake_workflow(store)
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


def test_entity_and_soldier_performance_endpoints_project_existing_records() -> None:
    store = InMemoryRunStore()
    workflow = fake_workflow(store)
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
                free_text=(
                    "Jones blew Phase Line Bird. Smith asleep at 0300. "
                    "Garcia textbook ambush rehearsal."
                ),
            )
        )
        asyncio.run(workflow.process(record.run_id))
        completed = store.get(record.run_id)
        assert completed is not None
        jones_recommendation = next(
            item
            for item in completed.recommendations
            if item.recommendation.target_soldier_id == "Jones"
        )
        workflow.approve(
            completed.run_id,
            jones_recommendation.recommendation.recommendation_id,
            approved=True,
        )

        soldier = main.get_soldier_entity("Jones")
        mission = main.get_mission_entity("m-1")
        mission_state = main.get_mission_state("m-1")
        performance = main.get_soldier_performance("Jones")
        recommendation_detail = main.get_recommendation(
            jones_recommendation.recommendation.recommendation_id
        )
        recent = main.list_recent_recommendations(mission_id="m-1")
        subgraph = main.get_graph_subgraph(mission_id="m-1")

        assert soldier.soldier_id == "Jones"
        assert soldier.observations[0].note
        assert soldier.update_refs
        assert mission.mission_id == "m-1"
        assert mission.soldier_ids == ["Garcia", "Jones", "Smith"]
        assert mission_state.mission_id == "m-1"
        assert mission_state.total_observations == 3
        assert mission_state.approved_recommendations == 1
        assert len(performance.approved_recommendations) == 1
        assert performance.pending_review_count == 0
        assert performance.recent_observations
        assert not hasattr(performance.recent_observations[0], "note")
        assert recommendation_detail.status == "approved"
        assert recommendation_detail.run_id == completed.run_id
        assert any(item.status == "approved" for item in recent)
        assert {node.kind for node in subgraph.nodes}.issuperset(
            {"Mission", "Platoon", "Soldier", "Observation", "Recommendation"}
        )
        assert any(edge.label == "DERIVED_FROM" for edge in subgraph.edges)
    finally:
        main.store = previous_store
        main.workflow = previous_workflow


def test_v1_decision_rejects_pending_recommendation() -> None:
    store = InMemoryRunStore()
    workflow = fake_workflow(store)
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
            _request("/v1/recommendations/test/decision"),
        )
        assert response.status == "rejected"
    finally:
        main.store = previous_store
        main.workflow = previous_workflow


def test_v1_decision_rejects_instructor_edit_that_fails_policy() -> None:
    store = InMemoryRunStore()
    workflow = fake_workflow(store)
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
        edited = pending.recommendation.model_copy(
            update={
                "target_soldier_id": "Unknown",
                "rationale": (
                    "Instructor edit intentionally points at an unvalidated target to "
                    "exercise the deterministic policy gate."
                ),
            }
        )

        with pytest.raises(main.HTTPException) as exc:
            main.decide_recommendation(
                pending.recommendation.recommendation_id,
                main.RecommendationDecision(
                    decision="approve",
                    edited_recommendation=edited,
                ),
                _request("/v1/recommendations/test/decision"),
            )

        assert exc.value.status_code == 409
        assert "edited recommendation failed policy" in exc.value.detail
        unchanged = store.get(record.run_id)
        assert unchanged is not None
        still_pending = next(
            item
            for item in unchanged.recommendations
            if item.recommendation.recommendation_id == pending.recommendation.recommendation_id
        )
        assert still_pending.status == "pending"
    finally:
        main.store = previous_store
        main.workflow = previous_workflow


def test_instructor_edit_approval_requires_decision_rationale() -> None:
    store = InMemoryRunStore()
    workflow = fake_workflow(store)
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
    edited = pending.recommendation.model_copy(
        update={
            "proposed_modification": (
                "At the next covered halt, have Jones issue a two-minute SITREP."
            )
        }
    )

    with pytest.raises(ValueError, match="decision_rationale"):
        workflow.approve(
            completed.run_id,
            pending.recommendation.recommendation_id,
            approved=True,
            edited_recommendation=edited,
        )


def test_api_exposes_only_versioned_operational_routes() -> None:
    paths = {
        path for route in main.app.routes if isinstance(path := getattr(route, "path", None), str)
    }
    assert "/v1/ingest" in paths
    assert "/v1/readyz" in paths
    assert "/v1/runs/{run_id}" in paths
    assert "/v1/missions/{mission_id}/state" in paths
    assert "/v1/entities/soldiers/{soldier_id}" in paths
    assert "/v1/entities/missions/{mission_id}" in paths
    assert "/v1/soldiers/{soldier_id}/performance" in paths
    assert "/v1/soldier/{soldier_id}/training-trajectory" in paths
    assert "/v1/runs/{run_id}/audit" in paths
    assert "/v1/recommendations/recent" in paths
    assert "/v1/recommendations/{recommendation_id}" in paths
    assert "/v1/recommendations/{recommendation_id}/decision" in paths
    assert "/v1/graph/subgraph" in paths
    assert "/v1/outbox" in paths
    assert "/v1/outbox/{event_id}/published" in paths
    assert "/v1/update-ledger" in paths
    assert "/v1/lessons-learned" in paths
    assert "/ingest" not in paths
    assert "/runs/{run_id}" not in paths
    assert "/healthz" not in paths


def test_process_records_error_when_run_lease_is_held() -> None:
    store = InMemoryRunStore()
    leases = InMemoryRunLease()
    workflow = fake_workflow(store, lease=leases)
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
    held = leases.acquire(record.run_id)
    try:
        asyncio.run(workflow.process(record.run_id))
    finally:
        held.release()

    updated = store.get(record.run_id)
    assert updated is not None
    assert updated.errors == ["run is already being processed"]


def test_outbox_events_can_be_marked_published() -> None:
    store = InMemoryRunStore()
    workflow = fake_workflow(store)
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
        workflow.approve(
            completed.run_id,
            pending.recommendation.recommendation_id,
            approved=True,
        )

        events = main.list_pending_outbox_events()
        assert len(events) == 1
        response = main.mark_outbox_event_published(events[0].event_id)

        assert response.status == "published"
        assert main.list_pending_outbox_events() == []
    finally:
        main.store = previous_store
        main.workflow = previous_workflow
