from src.agent.store import InMemoryRunStore, PostgresRunStore, build_run_store
from src.config import Settings
from src.contracts import (
    AuditEvent,
    DevelopmentEdge,
    GeoPoint,
    IngestEnvelope,
    OutboxEvent,
    Phase,
    PolicyDecision,
    RecommendationRecord,
    RiskLevel,
    RunRecord,
    RunStatus,
    ScenarioRecommendation,
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


def test_memory_store_tracks_audit_and_outbox_events() -> None:
    store = InMemoryRunStore()
    audit = AuditEvent(run_id="run-1", event_type="run_accepted")
    outbox = OutboxEvent(
        event_type="recommendation.approved",
        aggregate_id="rec-1",
        run_id="run-1",
    )

    store.append_audit_event(audit)
    store.append_outbox_event(outbox)

    assert store.list_audit_events("run-1") == [audit]
    assert store.list_outbox_events("run-1") == [outbox]
    assert store.list_pending_outbox_events() == [outbox]
    assert store.mark_outbox_event_published(outbox.event_id)
    assert store.list_pending_outbox_events() == []
    assert not store.mark_outbox_event_published("missing")
