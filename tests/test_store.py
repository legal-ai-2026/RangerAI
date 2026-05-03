from src.agent.store import InMemoryRunStore, PostgresRunStore, build_run_store
from src.config import Settings
from src.contracts import (
    AuditEvent,
    DevelopmentEdge,
    EvidenceRef,
    GeoPoint,
    IngestEnvelope,
    LessonsLearnedSignal,
    Observation,
    OutboxEvent,
    Phase,
    PolicyDecision,
    RecommendationRecord,
    RiskLevel,
    RunRecord,
    RunStatus,
    ScenarioRecommendation,
    UpdateLedgerEntry,
)


def test_build_run_store_defaults_to_memory_without_postgres_config() -> None:
    assert isinstance(build_run_store(Settings()), InMemoryRunStore)


def test_build_run_store_uses_postgres_when_configured() -> None:
    store = build_run_store(
        Settings(
            postgres_host="postgres",
            postgres_db="ranger",
            postgres_user="app",
            postgres_password="secret",
        )
    )
    assert isinstance(store, PostgresRunStore)


def test_build_run_store_uses_database_url_when_configured() -> None:
    store = build_run_store(Settings(database_url="postgresql://app:secret@postgres:5432/ranger"))
    assert isinstance(store, PostgresRunStore)
    assert store.dsn == "postgresql://app:secret@postgres:5432/ranger"


def test_memory_store_finds_run_by_recommendation_id() -> None:
    store = InMemoryRunStore()
    recommendation = ScenarioRecommendation(
        recommendation_id="rec-1",
        target_soldier_id="Jones",
        rationale="Observed task friction supports a focused supervised development event.",
        development_edge=DevelopmentEdge.communications,
        proposed_modification="Run a supervised five-point SITREP drill at the next halt.",
        doctrine_refs=["TC 3-21.76 MV-2"],
        safety_checks=["No immersion added."],
        estimated_duration_min=10,
        requires_resources=[],
        risk_level=RiskLevel.low,
        fairness_score=1.0,
    )
    record = RunRecord(
        run_id="run-1",
        status=RunStatus.pending_approval,
        ingest=IngestEnvelope(
            instructor_id="ri-1",
            platoon_id="plt-1",
            mission_id="m-1",
            phase=Phase.mountain,
            geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
            free_text="Jones needs a communications drill.",
        ),
        recommendations=[
            RecommendationRecord(
                recommendation=recommendation,
                policy=PolicyDecision(allowed=True, reasons=[], fairness_score=1.0),
            )
        ],
    )
    store.put(record)

    assert store.find_run_id_for_recommendation("rec-1") == "run-1"
    assert store.find_run_id_for_recommendation("missing") is None
    assert [item.run_id for item in store.list_runs_for_soldier("Jones")] == ["run-1"]
    assert store.list_runs_for_soldier("missing") == []
    assert [item.run_id for item in store.list_runs_for_mission("m-1")] == ["run-1"]
    assert store.list_runs_for_mission("missing") == []


def test_memory_store_finds_runs_by_observed_soldier() -> None:
    store = InMemoryRunStore()
    record = RunRecord(
        run_id="run-observed",
        status=RunStatus.pending_approval,
        ingest=IngestEnvelope(
            instructor_id="ri-1",
            platoon_id="plt-1",
            mission_id="m-2",
            phase=Phase.mountain,
            geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
            free_text="Smith asleep at 0300.",
        ),
        observations=[
            Observation(
                observation_id="obs-1",
                soldier_id="Smith",
                task_code="PB-7",
                note="Smith asleep at 0300.",
                rating="NOGO",
                source="free_text",
            )
        ],
    )
    store.put(record)

    assert [item.run_id for item in store.list_runs_for_soldier("Smith")] == ["run-observed"]


def test_memory_store_tracks_audit_and_outbox_events() -> None:
    store = InMemoryRunStore()
    audit = AuditEvent(run_id="run-1", event_type="run_accepted")
    outbox = OutboxEvent(
        event_type="recommendation.approved",
        aggregate_id="rec-1",
        run_id="run-1",
    )
    update = UpdateLedgerEntry(
        entity_type="recommendation",
        entity_id="rec-1",
        operation="approve",
        patch={"status": "approved"},
        content_hash_after="sha256:test",
    )

    store.append_audit_event(audit)
    store.append_outbox_event(outbox)
    store.append_update_event(update)

    assert store.list_audit_events("run-1") == [audit]
    assert store.list_outbox_events("run-1") == [outbox]
    assert store.list_pending_outbox_events() == [outbox]
    assert store.list_update_events() == [update]
    assert store.list_update_events(entity_type="recommendation") == [update]
    assert store.list_update_events(entity_id="rec-1") == [update]
    assert store.list_update_events(entity_id="missing") == []
    assert store.mark_outbox_event_published(outbox.event_id)
    assert store.list_pending_outbox_events() == []
    assert not store.mark_outbox_event_published("missing")


def test_memory_store_records_lesson_signals_idempotently() -> None:
    store = InMemoryRunStore()
    lesson = LessonsLearnedSignal(
        lesson_id="lesson-1",
        mission_id="m-1",
        summary="System 3 observed that post-contact reporting gaps affected follow-on planning.",
        evidence_refs=[EvidenceRef(ref="system3://lessons/lesson-1", role="source_lesson")],
    )

    assert store.put_lesson_signal(lesson)
    assert not store.put_lesson_signal(lesson)
    assert store.get_lesson_signal("lesson-1") == lesson
    assert store.get_lesson_signal("missing") is None
