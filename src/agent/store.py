from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from src.config import Settings, settings
from src.contracts import RunRecord


class RunStore(Protocol):
    def put(self, record: RunRecord) -> None:
        ...

    def get(self, run_id: str) -> RunRecord | None:
        ...

    def find_run_id_for_recommendation(self, recommendation_id: str) -> str | None:
        ...


@dataclass
class InMemoryRunStore:
    records: dict[str, RunRecord] = field(default_factory=dict)

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


@dataclass
class PostgresRunStore:
    host: str
    port: int
    dbname: str
    user: str
    password: str
    sslmode: str = "require"
    _schema_ready: bool = field(default=False, init=False, repr=False)

    @classmethod
    def from_settings(cls, config: Settings) -> "PostgresRunStore":
        if not config.postgres_configured:
            raise ValueError("Postgres run store requires POSTGRES_HOST, DB, USER, and PASSWORD")
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

    def _connect(self) -> Any:
        import psycopg

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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ranger_runs_status_idx ON ranger_runs (status)"
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ranger_runs_record_gin_idx
            ON ranger_runs USING gin (record jsonb_path_ops)
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
