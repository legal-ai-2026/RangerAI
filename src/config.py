from __future__ import annotations

import os
from dataclasses import dataclass

from src.agent.models import MODEL_CLAUDE_SONNET


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", MODEL_CLAUDE_SONNET)
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_multimodal_model: str = os.getenv("OPENAI_MULTIMODAL_MODEL", "gpt-4o")
    openai_stt_model: str = os.getenv("OPENAI_STT_MODEL", "whisper-1")
    deepgram_api_key: str | None = os.getenv("DEEPGRAM_API_KEY")
    mistral_api_key: str | None = os.getenv("MISTRAL_API_KEY")
    openweather_api_key: str | None = os.getenv("OPENWEATHER_API_KEY")
    falkordb_host: str = os.getenv("FALKORDB_HOST", "localhost")
    falkordb_port: int = int(os.getenv("FALKORDB_PORT", "6379"))
    falkordb_graph: str = os.getenv("FALKORDB_GRAPH", "ranger")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    langfuse_public_key: str | None = os.getenv("LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str | None = os.getenv("LANGFUSE_SECRET_KEY")
    langfuse_host: str | None = os.getenv("LANGFUSE_HOST")


settings = Settings()
