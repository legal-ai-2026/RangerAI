import asyncio

from src.config import Settings
from src.ingest.providers import ProviderClients


def test_provider_transcribe_uses_configured_openai_whisper_model(monkeypatch) -> None:
    provider = ProviderClients(settings=Settings(openai_api_key="test-key"))
    seen = {}

    def fake_openai_transcribe(_audio_b64: str, model: str):
        seen["model"] = model
        return "transcript"

    monkeypatch.setattr(provider, "_openai_transcribe", fake_openai_transcribe)
    assert asyncio.run(provider.transcribe("audio")) == "transcript"
    assert seen["model"] == "whisper-1"
