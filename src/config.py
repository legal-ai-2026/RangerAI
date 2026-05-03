from __future__ import annotations

import os
from dataclasses import dataclass, field

from src.agent.models import MODEL_CLAUDE_SONNET, MODEL_OPENAI_MULTIMODAL, MODEL_OPENAI_WHISPER


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = field(default=os.getenv("ANTHROPIC_API_KEY"), repr=False)
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", MODEL_CLAUDE_SONNET)
    openai_api_key: str | None = field(default=os.getenv("OPENAI_API_KEY"), repr=False)
    openai_multimodal_model: str = os.getenv("OPENAI_MULTIMODAL_MODEL", MODEL_OPENAI_MULTIMODAL)
    openai_stt_model: str = os.getenv("OPENAI_STT_MODEL", MODEL_OPENAI_WHISPER)
    deepgram_api_key: str | None = field(default=os.getenv("DEEPGRAM_API_KEY"), repr=False)
    mistral_api_key: str | None = field(default=os.getenv("MISTRAL_API_KEY"), repr=False)
    openweather_api_key: str | None = field(default=os.getenv("OPENWEATHER_API_KEY"), repr=False)

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
    falkordb_username: str | None = field(default=os.getenv("FALKORDB_USERNAME"), repr=False)
    falkordb_password: str | None = field(default=os.getenv("FALKORDB_PASSWORD"), repr=False)
    redis_url: str | None = os.getenv("REDIS_URL")
    langfuse_public_key: str | None = field(default=os.getenv("LANGFUSE_PUBLIC_KEY"), repr=False)
    langfuse_secret_key: str | None = field(default=os.getenv("LANGFUSE_SECRET_KEY"), repr=False)
    langfuse_host: str | None = os.getenv("LANGFUSE_HOST")

    @property
    def postgres_configured(self) -> bool:
        return all(
            [
                self.postgres_host,
                self.postgres_db,
                self.postgres_user,
                self.postgres_password,
            ]
        )


settings = Settings()
