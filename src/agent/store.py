from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from src.config import Settings, settings
from src.contracts import (
    AuditEvent,
    CalibrationSignal,
    LessonsLearnedSignal,
    OutboxEvent,
    RunRecord,
    UpdateLedgerEntry,
)


class RunStore(Protocol):
    def put(self, record: RunRecord) -> None: ...

    def get(self, run_id: str) -> RunRecord | None: ...

    def find_run_id_for_recommendation(self, recommendation_id: str) -> str | None: ...

    def list_runs_for_soldier(self, soldier_id: str, limit: int = 100) -> list[RunRecord]: ...

    def list_runs_for_mission(self, mission_id: str, limit: int = 100) -> list[RunRecord]: ...

    def list_recent_runs(self, limit: int = 100) -> list[RunRecord]: ...

    def health(self) -> bool: ...

    def append_audit_event(self, event: AuditEvent) -> None: ...

    def list_audit_events(self, run_id: str) -> list[AuditEvent]: ...

    def append_outbox_event(self, event: OutboxEvent) -> None: ...

    def list_outbox_events(self, run_id: str) -> list[OutboxEvent]: ...

    def list_pending_outbox_events(self, limit: int = 100) -> list[OutboxEvent]: ...

    def mark_outbox_event_published(self, event_id: str) -> bool: ...

    def append_update_event(self, event: UpdateLedgerEntry) -> None: ...

    def list_update_events(
        self,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 100,
    ) -> list[UpdateLedgerEntry]: ...

    def put_lesson_signal(self, lesson: LessonsLearnedSignal) -> bool: ...

    def get_lesson_signal(self, lesson_id: str) -> LessonsLearnedSignal | None: ...

    def put_calibration_signal(self, signal: CalibrationSignal) -> bool: ...

    def get_calibration_signal(self, signal_id: str) -> CalibrationSignal | None: ...

    def list_calibration_signals(
        self,
        target_soldier_id: str | None = None,
        recommendation_id: str | None = None,
        run_id: str | None = None,
        task_code: str | None = None,
        limit: int = 100,
    ) -> list[CalibrationSignal]: ...


@dataclass
class InMemoryRunStore:
    records: dict[str, RunRecord] = field(default_factory=dict)
    audit_events: dict[str, list[AuditEvent]] = field(default_factory=dict)
    outbox_events: dict[str, list[OutboxEvent]] = field(default_factory=dict)
    update_events: list[UpdateLedgerEntry] = field(default_factory=list)
    lesson_signals: dict[str, LessonsLearnedSignal] = field(default_factory=dict)
    calibration_signals: dict[str, CalibrationSignal] = field(default_factory=dict)

    def put(self, record: RunRecord) -> None:
        self.records[record.run_id] = record

    def get(self, run_id: str) -> RunRecord | None:
        return self.records.get(run_id)

    def find_run_id_for_recommendation(self, recommendation_id: str) -> str | None:
        for record in self.records.values():
            for item in record.recommendations:
                if item.recommendation.recommendation_id == recommendation_id:
                    return record.run_id
        return None

    def list_runs_for_soldier(self, soldier_id: str, limit: int = 100) -> list[RunRecord]:
        matches = [
            record
            for record in self.records.values()
            if _record_mentions_soldier(record, soldier_id)
        ]
        return sorted(matches, key=lambda record: record.ingest.timestamp_utc, reverse=True)[:limit]

    def list_runs_for_mission(self, mission_id: str, limit: int = 100) -> list[RunRecord]:
        matches = [
            record for record in self.records.values() if record.ingest.mission_id == mission_id
        ]
        return sorted(matches, key=lambda record: record.ingest.timestamp_utc, reverse=True)[:limit]

    def list_recent_runs(self, limit: int = 100) -> list[RunRecord]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        return sorted(
            self.records.values(),
            key=lambda record: record.ingest.timestamp_utc,
            reverse=True,
        )[:limit]

    def health(self) -> bool:
        return True

    def append_audit_event(self, event: AuditEvent) -> None:
        self.audit_events.setdefault(event.run_id, []).append(event)

    def list_audit_events(self, run_id: str) -> list[AuditEvent]:
        return sorted(
            self.audit_events.get(run_id, []),
            key=lambda event: event.timestamp_utc,
        )

    def append_outbox_event(self, event: OutboxEvent) -> None:
        self.outbox_events.setdefault(event.run_id, []).append(event)

    def list_outbox_events(self, run_id: str) -> list[OutboxEvent]:
        return sorted(
            self.outbox_events.get(run_id, []),
            key=lambda event: event.timestamp_utc,
        )

    def list_pending_outbox_events(self, limit: int = 100) -> list[OutboxEvent]:
        events = [
            event
            for run_events in self.outbox_events.values()
            for event in run_events
            if event.status == "pending"
        ]
        return sorted(events, key=lambda event: event.timestamp_utc)[:limit]

    def mark_outbox_event_published(self, event_id: str) -> bool:
        for run_id, run_events in self.outbox_events.items():
            for index, event in enumerate(run_events):
                if event.event_id != event_id:
                    continue
                self.outbox_events[run_id][index] = event.model_copy(update={"status": "published"})
                return True
        return False

    def append_update_event(self, event: UpdateLedgerEntry) -> None:
        if not any(item.version_id == event.version_id for item in self.update_events):
            self.update_events.append(event)

    def list_update_events(
        self,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 100,
    ) -> list[UpdateLedgerEntry]:
        events = self.update_events
        if entity_type is not None:
            events = [event for event in events if event.entity_type == entity_type]
        if entity_id is not None:
            events = [event for event in events if event.entity_id == entity_id]
        return sorted(events, key=lambda event: event.created_at_utc)[:limit]

    def put_lesson_signal(self, lesson: LessonsLearnedSignal) -> bool:
        if lesson.lesson_id in self.lesson_signals:
            return False
        self.lesson_signals[lesson.lesson_id] = lesson
        return True

    def get_lesson_signal(self, lesson_id: str) -> LessonsLearnedSignal | None:
        return self.lesson_signals.get(lesson_id)

    def put_calibration_signal(self, signal: CalibrationSignal) -> bool:
        if signal.signal_id in self.calibration_signals:
            return False
        self.calibration_signals[signal.signal_id] = signal
        return True

    def get_calibration_signal(self, signal_id: str) -> CalibrationSignal | None:
        return self.calibration_signals.get(signal_id)

    def list_calibration_signals(
        self,
        target_soldier_id: str | None = None,
        recommendation_id: str | None = None,
        run_id: str | None = None,
        task_code: str | None = None,
        limit: int = 100,
    ) -> list[CalibrationSignal]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        signals = list(self.calibration_signals.values())
        if target_soldier_id is not None:
            signals = [
                signal for signal in signals if signal.target_soldier_id == target_soldier_id
            ]
        if recommendation_id is not None:
            signals = [
                signal for signal in signals if signal.recommendation_id == recommendation_id
            ]
        if run_id is not None:
            signals = [signal for signal in signals if signal.run_id == run_id]
        if task_code is not None:
            signals = [signal for signal in signals if signal.task_code == task_code]
        return sorted(signals, key=lambda signal: signal.occurred_at_utc, reverse=True)[:limit]


@dataclass
class PostgresRunStore:
    host: str = ""
    port: int = 5432
    dbname: str = ""
    user: str = ""
    password: str = ""
    dsn: str | None = None
    sslmode: str = "require"
    _schema_ready: bool = field(default=False, init=False, repr=False)

    @classmethod
    def from_settings(cls, config: Settings) -> "PostgresRunStore":
        if not config.postgres_configured:
            raise ValueError("Postgres run store requires POSTGRES_HOST, DB, USER, and PASSWORD")
        if config.run_store_dsn:
            return cls(dsn=config.run_store_dsn)
        return cls(
            host=str(config.postgres_host),
            port=config.postgres_port,
            dbname=str(config.postgres_db),
            user=str(config.postgres_user),
            password=str(config.postgres_password),
            sslmode=config.postgres_sslmode,
        )

    def put(self, record: RunRecord) -> None:
        from psycopg.types.json import Jsonb

        payload = record.model_dump(mode="json")
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO ranger_runs (run_id, status, record, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (run_id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    record = EXCLUDED.record,
                    updated_at = now()
                """,
                (record.run_id, record.status.value, Jsonb(payload)),
            )

    def get(self, run_id: str) -> RunRecord | None:
        with self._connect() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                "SELECT record FROM ranger_runs WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return _record_from_payload(row[0])

    def find_run_id_for_recommendation(self, recommendation_id: str) -> str | None:
        from psycopg.types.json import Jsonb

        query = {
            "recommendations": [
                {"recommendation": {"recommendation_id": recommendation_id}},
            ],
        }
        with self._connect() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                "SELECT run_id FROM ranger_runs WHERE record @> %s LIMIT 1",
                (Jsonb(query),),
            ).fetchone()
        return None if row is None else str(row[0])

    def list_runs_for_soldier(self, soldier_id: str, limit: int = 100) -> list[RunRecord]:
        from psycopg.types.json import Jsonb

        if limit < 1:
            raise ValueError("limit must be at least 1")
        observation_query = {"observations": [{"soldier_id": soldier_id}]}
        recommendation_query = {
            "recommendations": [
                {"recommendation": {"target_soldier_id": soldier_id}},
            ],
        }
        target_ids_query = {
            "recommendations": [
                {"recommendation": {"target_ids": {"soldier_id": soldier_id}}},
            ],
        }
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT record
                FROM ranger_runs
                WHERE record @> %s OR record @> %s OR record @> %s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (
                    Jsonb(observation_query),
                    Jsonb(recommendation_query),
                    Jsonb(target_ids_query),
                    limit,
                ),
            ).fetchall()
        return [_record_from_payload(row[0]) for row in rows]

    def list_runs_for_mission(self, mission_id: str, limit: int = 100) -> list[RunRecord]:
        from psycopg.types.json import Jsonb

        if limit < 1:
            raise ValueError("limit must be at least 1")
        query = {"ingest": {"mission_id": mission_id}}
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT record
                FROM ranger_runs
                WHERE record @> %s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (Jsonb(query), limit),
            ).fetchall()
        return [_record_from_payload(row[0]) for row in rows]

    def list_recent_runs(self, limit: int = 100) -> list[RunRecord]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT record
                FROM ranger_runs
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [_record_from_payload(row[0]) for row in rows]

    def health(self) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def append_audit_event(self, event: AuditEvent) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO ranger_audit_events (
                    event_id,
                    run_id,
                    event_type,
                    actor_id,
                    recommendation_id,
                    trace_id,
                    payload,
                    timestamp_utc
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    event.event_id,
                    event.run_id,
                    event.event_type,
                    event.actor_id,
                    event.recommendation_id,
                    event.trace_id,
                    Jsonb(event.payload),
                    event.timestamp_utc,
                ),
            )

    def list_audit_events(self, run_id: str) -> list[AuditEvent]:
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT
                    event_id,
                    run_id,
                    event_type,
                    actor_id,
                    recommendation_id,
                    trace_id,
                    payload,
                    timestamp_utc
                FROM ranger_audit_events
                WHERE run_id = %s
                ORDER BY timestamp_utc ASC
                """,
                (run_id,),
            ).fetchall()
        return [
            AuditEvent(
                event_id=row[0],
                run_id=row[1],
                event_type=row[2],
                actor_id=row[3],
                recommendation_id=row[4],
                trace_id=row[5],
                payload=row[6],
                timestamp_utc=row[7],
            )
            for row in rows
        ]

    def append_outbox_event(self, event: OutboxEvent) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO ranger_outbox_events (
                    event_id,
                    event_type,
                    aggregate_id,
                    run_id,
                    trace_id,
                    payload,
                    status,
                    timestamp_utc
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.aggregate_id,
                    event.run_id,
                    event.trace_id,
                    Jsonb(event.payload),
                    event.status,
                    event.timestamp_utc,
                ),
            )

    def list_outbox_events(self, run_id: str) -> list[OutboxEvent]:
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT
                    event_id,
                    event_type,
                    aggregate_id,
                    run_id,
                    trace_id,
                    payload,
                    status,
                    timestamp_utc
                FROM ranger_outbox_events
                WHERE run_id = %s
                ORDER BY timestamp_utc ASC
                """,
                (run_id,),
            ).fetchall()
        return [_outbox_event_from_row(row) for row in rows]

    def list_pending_outbox_events(self, limit: int = 100) -> list[OutboxEvent]:
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT
                    event_id,
                    event_type,
                    aggregate_id,
                    run_id,
                    trace_id,
                    payload,
                    status,
                    timestamp_utc
                FROM ranger_outbox_events
                WHERE status = 'pending'
                ORDER BY timestamp_utc ASC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [_outbox_event_from_row(row) for row in rows]

    def mark_outbox_event_published(self, event_id: str) -> bool:
        with self._connect() as conn:
            self._ensure_schema(conn)
            result = conn.execute(
                """
                UPDATE ranger_outbox_events
                SET status = 'published'
                WHERE event_id = %s AND status = 'pending'
                """,
                (event_id,),
            )
        return result.rowcount > 0

    def append_update_event(self, event: UpdateLedgerEntry) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO ranger_update_ledger (
                    version_id,
                    entity_type,
                    entity_id,
                    source_service,
                    operation,
                    trace_id,
                    base_version_id,
                    patch,
                    source_refs,
                    content_hash_before,
                    content_hash_after,
                    created_at_utc
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (version_id) DO NOTHING
                """,
                (
                    event.version_id,
                    event.entity_type,
                    event.entity_id,
                    event.source_service,
                    event.operation,
                    event.trace_id,
                    event.base_version_id,
                    Jsonb(event.patch),
                    Jsonb(event.source_refs),
                    event.content_hash_before,
                    event.content_hash_after,
                    event.created_at_utc,
                ),
            )

    def list_update_events(
        self,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 100,
    ) -> list[UpdateLedgerEntry]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        filters: list[str] = []
        params: list[object] = []
        if entity_type is not None:
            filters.append("entity_type = %s")
            params.append(entity_type)
        if entity_id is not None:
            filters.append("entity_id = %s")
            params.append(entity_id)
        where_clause = "WHERE " + " AND ".join(filters) if filters else ""
        params.append(limit)
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                f"""
                SELECT
                    version_id,
                    entity_type,
                    entity_id,
                    source_service,
                    operation,
                    trace_id,
                    base_version_id,
                    patch,
                    source_refs,
                    content_hash_before,
                    content_hash_after,
                    created_at_utc
                FROM ranger_update_ledger
                {where_clause}
                ORDER BY created_at_utc ASC
                LIMIT %s
                """,
                params,
            ).fetchall()
        return [_update_event_from_row(row) for row in rows]

    def put_lesson_signal(self, lesson: LessonsLearnedSignal) -> bool:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            self._ensure_schema(conn)
            result = conn.execute(
                """
                INSERT INTO ranger_lesson_signals (
                    lesson_id,
                    payload,
                    source_system,
                    created_at_utc
                )
                VALUES (%s, %s, %s, now())
                ON CONFLICT (lesson_id) DO NOTHING
                """,
                (
                    lesson.lesson_id,
                    Jsonb(lesson.model_dump(mode="json")),
                    lesson.source_system,
                ),
            )
        return result.rowcount > 0

    def get_lesson_signal(self, lesson_id: str) -> LessonsLearnedSignal | None:
        with self._connect() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                "SELECT payload FROM ranger_lesson_signals WHERE lesson_id = %s",
                (lesson_id,),
            ).fetchone()
        if row is None:
            return None
        return _lesson_signal_from_payload(row[0])

    def put_calibration_signal(self, signal: CalibrationSignal) -> bool:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            self._ensure_schema(conn)
            result = conn.execute(
                """
                INSERT INTO ranger_calibration_signals (
                    signal_id,
                    recommendation_id,
                    run_id,
                    target_soldier_id,
                    task_code,
                    intervention_id,
                    outcome,
                    payload,
                    occurred_at_utc,
                    created_at_utc
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (signal_id) DO NOTHING
                """,
                (
                    signal.signal_id,
                    signal.recommendation_id,
                    signal.run_id,
                    signal.target_soldier_id,
                    signal.task_code,
                    signal.intervention_id,
                    signal.outcome,
                    Jsonb(signal.model_dump(mode="json")),
                    signal.occurred_at_utc,
                ),
            )
        return result.rowcount > 0

    def get_calibration_signal(self, signal_id: str) -> CalibrationSignal | None:
        with self._connect() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                "SELECT payload FROM ranger_calibration_signals WHERE signal_id = %s",
                (signal_id,),
            ).fetchone()
        if row is None:
            return None
        return _calibration_signal_from_payload(row[0])

    def list_calibration_signals(
        self,
        target_soldier_id: str | None = None,
        recommendation_id: str | None = None,
        run_id: str | None = None,
        task_code: str | None = None,
        limit: int = 100,
    ) -> list[CalibrationSignal]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        filters: list[str] = []
        params: list[object] = []
        if target_soldier_id is not None:
            filters.append("target_soldier_id = %s")
            params.append(target_soldier_id)
        if recommendation_id is not None:
            filters.append("recommendation_id = %s")
            params.append(recommendation_id)
        if run_id is not None:
            filters.append("run_id = %s")
            params.append(run_id)
        if task_code is not None:
            filters.append("task_code = %s")
            params.append(task_code)
        where_clause = "WHERE " + " AND ".join(filters) if filters else ""
        params.append(limit)
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                f"""
                SELECT payload
                FROM ranger_calibration_signals
                {where_clause}
                ORDER BY occurred_at_utc DESC
                LIMIT %s
                """,
                params,
            ).fetchall()
        return [_calibration_signal_from_payload(row[0]) for row in rows]

    def _connect(self) -> Any:
        import psycopg

        if self.dsn:
            return psycopg.connect(self.dsn, connect_timeout=5)
        return psycopg.connect(
            host=self.host,
            port=self.port,
            dbname=self.dbname,
            user=self.user,
            password=self.password,
            sslmode=self.sslmode,
            connect_timeout=5,
        )

    def _ensure_schema(self, conn: Any) -> None:
        if self._schema_ready:
            return
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ranger_runs (
                run_id text PRIMARY KEY,
                status text NOT NULL,
                record jsonb NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ranger_runs_status_idx ON ranger_runs (status)")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ranger_runs_record_gin_idx
            ON ranger_runs USING gin (record jsonb_path_ops)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ranger_audit_events (
                event_id text PRIMARY KEY,
                run_id text NOT NULL,
                event_type text NOT NULL,
                actor_id text,
                recommendation_id text,
                trace_id text,
                payload jsonb NOT NULL,
                timestamp_utc timestamptz NOT NULL
            )
            """
        )
        conn.execute("ALTER TABLE ranger_audit_events ADD COLUMN IF NOT EXISTS trace_id text")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ranger_audit_events_run_id_idx
            ON ranger_audit_events (run_id, timestamp_utc)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ranger_outbox_events (
                event_id text PRIMARY KEY,
                event_type text NOT NULL,
                aggregate_id text NOT NULL,
                run_id text NOT NULL,
                trace_id text,
                payload jsonb NOT NULL,
                status text NOT NULL,
                timestamp_utc timestamptz NOT NULL
            )
            """
        )
        conn.execute("ALTER TABLE ranger_outbox_events ADD COLUMN IF NOT EXISTS trace_id text")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ranger_outbox_events_status_idx
            ON ranger_outbox_events (status, timestamp_utc)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ranger_update_ledger (
                version_id text PRIMARY KEY,
                entity_type text NOT NULL,
                entity_id text NOT NULL,
                source_service text NOT NULL,
                operation text NOT NULL,
                trace_id text,
                base_version_id text,
                patch jsonb NOT NULL,
                source_refs jsonb NOT NULL,
                content_hash_before text,
                content_hash_after text NOT NULL,
                created_at_utc timestamptz NOT NULL
            )
            """
        )
        conn.execute("ALTER TABLE ranger_update_ledger ADD COLUMN IF NOT EXISTS trace_id text")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ranger_update_ledger_entity_idx
            ON ranger_update_ledger (entity_type, entity_id, created_at_utc)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ranger_lesson_signals (
                lesson_id text PRIMARY KEY,
                payload jsonb NOT NULL,
                source_system text NOT NULL,
                created_at_utc timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ranger_lesson_signals_source_idx
            ON ranger_lesson_signals (source_system, created_at_utc)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ranger_calibration_signals (
                signal_id text PRIMARY KEY,
                recommendation_id text NOT NULL,
                run_id text NOT NULL,
                target_soldier_id text,
                task_code text,
                intervention_id text,
                outcome text NOT NULL,
                payload jsonb NOT NULL,
                occurred_at_utc timestamptz NOT NULL,
                created_at_utc timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ranger_calibration_signals_target_idx
            ON ranger_calibration_signals (target_soldier_id, occurred_at_utc)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ranger_calibration_signals_recommendation_idx
            ON ranger_calibration_signals (recommendation_id, occurred_at_utc)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ranger_calibration_signals_run_idx
            ON ranger_calibration_signals (run_id, occurred_at_utc)
            """
        )
        self._schema_ready = True


def build_run_store(config: Settings = settings) -> RunStore:
    if config.postgres_configured:
        return PostgresRunStore.from_settings(config)
    return InMemoryRunStore()


def _record_from_payload(payload: Any) -> RunRecord:
    if isinstance(payload, str):
        return RunRecord.model_validate_json(payload)
    return RunRecord.model_validate(payload)


def _lesson_signal_from_payload(payload: Any) -> LessonsLearnedSignal:
    if isinstance(payload, str):
        return LessonsLearnedSignal.model_validate_json(payload)
    return LessonsLearnedSignal.model_validate(payload)


def _calibration_signal_from_payload(payload: Any) -> CalibrationSignal:
    if isinstance(payload, str):
        return CalibrationSignal.model_validate_json(payload)
    return CalibrationSignal.model_validate(payload)


def _record_mentions_soldier(record: RunRecord, soldier_id: str) -> bool:
    if any(observation.soldier_id == soldier_id for observation in record.observations):
        return True
    return any(
        item.recommendation.target_soldier_id == soldier_id
        or item.recommendation.target_ids.soldier_id == soldier_id
        for item in record.recommendations
    )


def _outbox_event_from_row(row: Any) -> OutboxEvent:
    return OutboxEvent(
        event_id=row[0],
        event_type=row[1],
        aggregate_id=row[2],
        run_id=row[3],
        trace_id=row[4],
        payload=row[5],
        status=row[6],
        timestamp_utc=row[7],
    )


def _update_event_from_row(row: Any) -> UpdateLedgerEntry:
    return UpdateLedgerEntry(
        version_id=row[0],
        entity_type=row[1],
        entity_id=row[2],
        source_service=row[3],
        operation=row[4],
        trace_id=row[5],
        base_version_id=row[6],
        patch=row[7],
        source_refs=row[8],
        content_hash_before=row[9],
        content_hash_after=row[10],
        created_at_utc=row[11],
    )
