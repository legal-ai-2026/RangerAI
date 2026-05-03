import asyncio

from src.config import Settings
from src.contracts import Observation
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


def test_provider_drafts_from_intervention_library_before_model(monkeypatch) -> None:
    provider = ProviderClients(settings=Settings(anthropic_api_key="test-key"))

    def fail_if_called(_instruction: str, _content: str):
        raise AssertionError("free-form model drafting should not run before library retrieval")

    monkeypatch.setattr(provider, "_claude_json", fail_if_called)

    recommendations = asyncio.run(
        provider.draft_recommendations(
            [
                Observation(
                    soldier_id="Jones",
                    task_code="MV-2",
                    note="Jones blew Phase Line Bird and gave no SITREP.",
                    rating="NOGO",
                    source="free_text",
                )
            ]
        )
    )

    assert recommendations[0].intervention_id == "comm_degraded_sitrep"
