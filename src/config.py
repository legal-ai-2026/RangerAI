from __future__ import annotations

import os
from dataclasses import dataclass, field

from src.agent.models import (
    MODEL_CLAUDE_SONNET,
    MODEL_OPENAI_MULTIMODAL,
    MODEL_OPENAI_REASONING,
    MODEL_OPENAI_WHISPER,
)


def csv_env(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def bool_env(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def psycopg_dsn(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url.removeprefix("postgresql+psycopg://")
    return url


@dataclass(frozen=True)
class Settings:
    system1_api_key: str | None = field(default=os.getenv("SYSTEM1_API_KEY"), repr=False)
    cors_allow_origins: tuple[str, ...] = field(
        default_factory=lambda: csv_env(os.getenv("CORS_ALLOW_ORIGINS"))
    )

    anthropic_api_key: str | None = field(default=os.getenv("ANTHROPIC_API_KEY"), repr=False)
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", MODEL_CLAUDE_SONNET)
    openai_api_key: str | None = field(default=os.getenv("OPENAI_API_KEY"), repr=False)
    openai_multimodal_model: str = os.getenv("OPENAI_MULTIMODAL_MODEL", MODEL_OPENAI_MULTIMODAL)
    openai_extraction_model: str = os.getenv(
        "OPENAI_EXTRACTION_MODEL",
        os.getenv("OPENAI_REASONING_MODEL", MODEL_OPENAI_REASONING),
    )
    openai_reasoning_model: str = os.getenv("OPENAI_REASONING_MODEL", MODEL_OPENAI_REASONING)
    openai_stt_model: str = os.getenv("OPENAI_STT_MODEL", MODEL_OPENAI_WHISPER)
    deepgram_api_key: str | None = field(default=os.getenv("DEEPGRAM_API_KEY"), repr=False)
    mistral_api_key: str | None = field(default=os.getenv("MISTRAL_API_KEY"), repr=False)
    openweather_api_key: str | None = field(default=os.getenv("OPENWEATHER_API_KEY"), repr=False)

    database_url: str | None = field(default=os.getenv("DATABASE_URL"), repr=False)
    pgvector_connection_string: str | None = field(
        default=os.getenv("PGVECTOR_CONNECTION_STRING"),
        repr=False,
    )
    postgres_host: str | None = os.getenv("POSTGRES_HOST")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    postgres_db: str | None = os.getenv("POSTGRES_DB")
    postgres_user: str | None = field(default=os.getenv("POSTGRES_USER"), repr=False)
    postgres_password: str | None = field(default=os.getenv("POSTGRES_PASSWORD"), repr=False)
    postgres_sslmode: str = os.getenv("POSTGRES_SSLMODE", "require")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))

    falkordb_host: str = os.getenv("FALKORDB_HOST", "localhost")
    falkordb_port: int = int(os.getenv("FALKORDB_PORT", "6379"))
    falkordb_graph: str = os.getenv("FALKORDB_GRAPH", "ranger")
    falkordb_url: str | None = field(default=os.getenv("FALKORDB_URL"), repr=False)
    falkordb_username: str | None = field(default=os.getenv("FALKORDB_USERNAME"), repr=False)
    falkordb_password: str | None = field(default=os.getenv("FALKORDB_PASSWORD"), repr=False)
    redis_url: str | None = os.getenv("REDIS_URL")
    langfuse_public_key: str | None = field(default=os.getenv("LANGFUSE_PUBLIC_KEY"), repr=False)
    langfuse_secret_key: str | None = field(default=os.getenv("LANGFUSE_SECRET_KEY"), repr=False)
    langfuse_host: str | None = os.getenv("LANGFUSE_HOST")
    weather_provider: str = os.getenv("WEATHER_PROVIDER", "synthetic")
    terrain_provider: str = os.getenv("TERRAIN_PROVIDER", "synthetic")
    nws_user_agent: str | None = os.getenv("NWS_USER_AGENT")
    environment_timeout_seconds: float = float(os.getenv("ENVIRONMENT_TIMEOUT_SECONDS", "3"))
    allow_synthetic_environment_fallback: bool = bool_env(
        os.getenv("ALLOW_SYNTHETIC_ENVIRONMENT_FALLBACK"),
        True,
    )

    @property
    def postgres_configured(self) -> bool:
        if self.database_url or self.pgvector_connection_string:
            return True
        return all(
            [
                self.postgres_host,
                self.postgres_db,
                self.postgres_user,
                self.postgres_password,
            ]
        )

    @property
    def run_store_dsn(self) -> str | None:
        return psycopg_dsn(self.database_url)

    @property
    def vector_store_dsn(self) -> str | None:
        return psycopg_dsn(self.pgvector_connection_string or self.database_url)


settings = Settings()
