from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import Field

from src.config import Settings, settings
from src.contracts import StrictModel


class VectorDocument(StrictModel):
    namespace: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    embedding: list[float] = Field(min_length=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class VectorSearchResult(StrictModel):
    namespace: str
    document_id: str
    text: str
    metadata: dict[str, object] = Field(default_factory=dict)
    distance: float


@dataclass
class PgVectorStore:
    host: str = ""
    port: int = 5432
    dbname: str = ""
    user: str = ""
    password: str = ""
    dsn: str | None = None
    sslmode: str = "require"
    dimensions: int = 1536
    _schema_ready: bool = False

    def __post_init__(self) -> None:
        if self.dimensions <= 0:
            raise ValueError("embedding dimensions must be positive")

    @classmethod
    def from_settings(cls, config: Settings) -> "PgVectorStore":
        if not config.postgres_configured:
            raise ValueError("pgvector store requires POSTGRES_HOST, DB, USER, and PASSWORD")
        if config.vector_store_dsn:
            return cls(dsn=config.vector_store_dsn, dimensions=config.embedding_dimensions)
        return cls(
            host=str(config.postgres_host),
            port=config.postgres_port,
            dbname=str(config.postgres_db),
            user=str(config.postgres_user),
            password=str(config.postgres_password),
            sslmode=config.postgres_sslmode,
            dimensions=config.embedding_dimensions,
        )

    def upsert(self, document: VectorDocument) -> None:
        self._validate_dimensions(document.embedding)
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO ranger_vector_documents (
                    namespace,
                    document_id,
                    text,
                    metadata,
                    embedding,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s::vector, now())
                ON CONFLICT (namespace, document_id)
                DO UPDATE SET
                    text = EXCLUDED.text,
                    metadata = EXCLUDED.metadata,
                    embedding = EXCLUDED.embedding,
                    updated_at = now()
                """,
                (
                    document.namespace,
                    document.document_id,
                    document.text,
                    Jsonb(document.metadata),
                    vector_literal(document.embedding),
                ),
            )

    def search(
        self,
        namespace: str,
        embedding: list[float],
        limit: int = 5,
    ) -> list[VectorSearchResult]:
        self._validate_dimensions(embedding)
        if limit < 1:
            raise ValueError("limit must be at least 1")
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT
                    namespace,
                    document_id,
                    text,
                    metadata,
                    embedding <=> %s::vector AS distance
                FROM ranger_vector_documents
                WHERE namespace = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (
                    vector_literal(embedding),
                    namespace,
                    vector_literal(embedding),
                    limit,
                ),
            ).fetchall()
        return [
            VectorSearchResult(
                namespace=row[0],
                document_id=row[1],
                text=row[2],
                metadata=row[3],
                distance=float(row[4]),
            )
            for row in rows
        ]

    def health(self) -> bool:
        try:
            with self._connect() as conn:
                self._ensure_schema(conn)
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

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
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS ranger_vector_documents (
                namespace text NOT NULL,
                document_id text NOT NULL,
                text text NOT NULL,
                metadata jsonb NOT NULL,
                embedding vector({self.dimensions}) NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (namespace, document_id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ranger_vector_documents_namespace_idx
            ON ranger_vector_documents (namespace)
            """
        )
        self._schema_ready = True

    def _validate_dimensions(self, embedding: list[float]) -> None:
        if len(embedding) != self.dimensions:
            raise ValueError(
                f"embedding has {len(embedding)} dimensions; expected {self.dimensions}"
            )


def build_vector_store(config: Settings = settings) -> PgVectorStore | None:
    if not config.postgres_configured:
        return None
    return PgVectorStore.from_settings(config)


def vector_literal(values: list[float]) -> str:
    if not values:
        raise ValueError("embedding cannot be empty")
    floats = [float(value) for value in values]
    if not all(isfinite(value) for value in floats):
        raise ValueError("embedding values must be finite")
    return "[" + ",".join(str(value) for value in floats) + "]"
