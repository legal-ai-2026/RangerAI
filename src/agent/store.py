from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from src.config import Settings, settings
from src.contracts import AuditEvent, OutboxEvent, RunRecord, UpdateLedgerEntry


class RunStore(Protocol):
    def put(self, record: RunRecord) -> None: ...

    def get(self, run_id: str) -> RunRecord | None: ...

    def find_run_id_for_recommendation(self, recommendation_id: str) -> str | None: ...

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


@dataclass
class InMemoryRunStore:
    records: dict[str, RunRecord] = field(default_factory=dict)
    audit_events: dict[str, list[AuditEvent]] = field(default_factory=dict)
    outbox_events: dict[str, list[OutboxEvent]] = field(default_factory=dict)
    update_events: list[UpdateLedgerEntry] = field(default_factory=list)

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
                    payload,
                    timestamp_utc
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    event.event_id,
                    event.run_id,
                    event.event_type,
                    event.actor_id,
                    event.recommendation_id,
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
                payload=row[5],
                timestamp_utc=row[6],
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
                    payload,
                    status,
                    timestamp_utc
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.aggregate_id,
                    event.run_id,
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
                    payload,
                    status,
                    timestamp_utc
                FROM ranger_outbox_events
                WHERE run_id = %s
                ORDER BY timestamp_utc ASC
                """,
                (run_id,),
            ).fetchall()
        return [
            OutboxEvent(
                event_id=row[0],
                event_type=row[1],
                aggregate_id=row[2],
                run_id=row[3],
                payload=row[4],
                status=row[5],
                timestamp_utc=row[6],
            )
            for row in rows
        ]

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
                    base_version_id,
                    patch,
                    source_refs,
                    content_hash_before,
                    content_hash_after,
                    created_at_utc
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (version_id) DO NOTHING
                """,
                (
                    event.version_id,
                    event.entity_type,
                    event.entity_id,
                    event.source_service,
                    event.operation,
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
                payload jsonb NOT NULL,
                timestamp_utc timestamptz NOT NULL
            )
            """
        )
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
                payload jsonb NOT NULL,
                status text NOT NULL,
                timestamp_utc timestamptz NOT NULL
            )
            """
        )
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
                base_version_id text,
                patch jsonb NOT NULL,
                source_refs jsonb NOT NULL,
                content_hash_before text,
                content_hash_after text NOT NULL,
                created_at_utc timestamptz NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ranger_update_ledger_entity_idx
            ON ranger_update_ledger (entity_type, entity_id, created_at_utc)
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


def _outbox_event_from_row(row: Any) -> OutboxEvent:
    return OutboxEvent(
        event_id=row[0],
        event_type=row[1],
        aggregate_id=row[2],
        run_id=row[3],
        payload=row[4],
        status=row[5],
        timestamp_utc=row[6],
    )


def _update_event_from_row(row: Any) -> UpdateLedgerEntry:
    return UpdateLedgerEntry(
        version_id=row[0],
        entity_type=row[1],
        entity_id=row[2],
        source_service=row[3],
        operation=row[4],
        base_version_id=row[5],
        patch=row[6],
        source_refs=row[7],
        content_hash_before=row[8],
        content_hash_after=row[9],
        created_at_utc=row[10],
    )
