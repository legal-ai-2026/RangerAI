import asyncio
import os
from types import SimpleNamespace

import pytest

from src.agent.reasoning import ModelRecommendationDraft, build_reasoning_context
from src.agent.policy import PolicyEngine, observations_to_roster
from src.config import Settings
from src.contracts import GeoPoint, IngestEnvelope, Observation, Phase
from src.ingest.providers import ProviderClients
from src.ingest.providers import _openai_json_object
from src.ingest.providers import heuristic_observations


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


def test_heuristic_extraction_scores_each_soldier_sentence_independently() -> None:
    observations = heuristic_observations(
        "Jones blew Phase Line Bird and gave no SITREP. "
        "Smith asleep at 0300 during patrol-base security. "
        "Garcia textbook ambush rehearsal."
    )

    ratings = {item.soldier_id: item.rating for item in observations}

    assert ratings == {"Jones": "NOGO", "Smith": "NOGO", "Garcia": "GO"}


def test_provider_applies_valid_model_draft_to_library_candidate(monkeypatch) -> None:
    provider = ProviderClients(settings=Settings(openai_api_key="test-key"))
    observations = [
        Observation(
            soldier_id="Jones",
            task_code="MV-2",
            note="Jones blew Phase Line Bird and gave no SITREP.",
            rating="NOGO",
            source="free_text",
        )
    ]
    context = build_reasoning_context(
        run_id="run-model",
        ingest=_ingest(),
        observations=observations,
        ocr_pages=[],
        kg_observation_refs={},
        extraction_uncertainties=[],
        errors=[],
    )

    def model_drafts(_context):
        return [
            ModelRecommendationDraft(
                rank=1,
                intervention_id="comm_degraded_sitrep",
                target_soldier_id="Jones",
                confidence=0.88,
                evidence_summary="Jones missed the movement report standard.",
                why_now="The next halt gives the instructor an immediate reporting repetition.",
                expected_learning_signal="Jones issues a concise SITREP and FRAGO without prompting.",
                risk_controls="Keep the event short, supervised, and free of added physical load.",
            )
        ]

    monkeypatch.setattr(provider, "_openai_recommendation_drafts", model_drafts)

    recommendations = asyncio.run(provider.draft_recommendations(observations, context))

    assert len(recommendations) == 1
    assert recommendations[0].evidence_summary == "Jones missed the movement report standard."
    assert recommendations[0].why_now is not None
    assert recommendations[0].model_context_refs == [
        "model://openai/gpt-5.4-mini#recommendation_rank_1"
    ]
    assert provider.diagnostics[-1].stage == "recommendation_ranking"
    assert provider.diagnostics[-1].provider == "openai"
    assert provider.diagnostics[-1].status == "applied"


def test_openai_json_object_retries_non_chat_models_with_responses_api() -> None:
    seen = {}

    class FakeChatCompletions:
        def create(self, **_kwargs):
            raise RuntimeError("This is not a chat model and thus not supported")

    class FakeResponses:
        def create(self, **kwargs):
            seen["responses"] = kwargs
            return SimpleNamespace(output_text='{"ok": true}')

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeChatCompletions()),
        responses=FakeResponses(),
    )

    parsed = _openai_json_object(
        client=client,
        model="gpt-5.5-pro",
        system_content="Return only JSON.",
        user_content='Return {"ok": true}',
    )

    assert parsed == {"ok": True}
    assert seen["responses"]["model"] == "gpt-5.5-pro"
    assert seen["responses"]["text"] == {"format": {"type": "json_object"}}


def test_openai_extraction_uses_configured_extraction_model(monkeypatch) -> None:
    provider = ProviderClients(
        settings=Settings(
            openai_api_key="test-key",
            openai_extraction_model="gpt-fast-extract",
            openai_reasoning_model="gpt-slow-rank",
        )
    )
    seen = {}

    def fake_json_object(**kwargs):
        seen["model"] = kwargs["model"]
        return {
            "observations": [
                {
                    "soldier_id": "Jones",
                    "task_code": "MV-2",
                    "note": "Jones missed the SITREP.",
                    "rating": "NOGO",
                    "source": "free_text",
                }
            ],
            "uncertainties": [],
        }

    monkeypatch.setattr("openai.OpenAI", lambda api_key: object())
    monkeypatch.setattr("src.ingest.providers._openai_json_object", fake_json_object)

    extracted = provider._openai_extract_observations("Jones missed the SITREP.")

    assert seen["model"] == "gpt-fast-extract"
    assert extracted.observations[0].soldier_id == "Jones"


def test_openai_extraction_normalizes_unknown_uncertainty_type(monkeypatch) -> None:
    provider = ProviderClients(settings=Settings(openai_api_key="test-key"))

    def fake_json_object(**_kwargs):
        return {
            "observations": [
                {
                    "soldier_id": "Jones",
                    "task_code": "MV-2",
                    "note": "Jones missed the SITREP.",
                    "rating": "NOGO",
                    "source": "free_text",
                }
            ],
            "uncertainties": [
                {
                    "source_ref": "input://text",
                    "uncertainty_type": "task_code_unmapped",
                    "confidence": 0.4,
                    "note": "Model used a non-contract uncertainty label.",
                    "soldier_id": "Jones",
                    "task_code": "MV-2",
                }
            ],
        }

    monkeypatch.setattr("openai.OpenAI", lambda api_key: object())
    monkeypatch.setattr("src.ingest.providers._openai_json_object", fake_json_object)

    extracted = provider._openai_extract_observations("Jones missed the SITREP.")

    assert extracted.observations[0].soldier_id == "Jones"
    assert extracted.uncertainties[0].uncertainty_type == "ambiguous_text"


def test_openai_extraction_derives_unknown_task_code_from_note(monkeypatch) -> None:
    provider = ProviderClients(settings=Settings(openai_api_key="test-key"))

    def fake_json_object(**_kwargs):
        return {
            "observations": [
                {
                    "soldier_id": "Jones",
                    "task_code": "UNKNOWN",
                    "note": "Jones blew Phase Line Bird and gave no SITREP.",
                    "rating": "NOGO",
                    "source": "free_text",
                }
            ],
            "uncertainties": [],
        }

    monkeypatch.setattr("openai.OpenAI", lambda api_key: object())
    monkeypatch.setattr("src.ingest.providers._openai_json_object", fake_json_object)

    extracted = provider._openai_extract_observations("Jones missed the SITREP.")

    assert extracted.observations[0].task_code == "MV-2"


def test_invalid_model_draft_falls_back_to_deterministic_recommendation(monkeypatch) -> None:
    provider = ProviderClients(settings=Settings(openai_api_key="test-key"))
    observations = [
        Observation(
            soldier_id="Jones",
            task_code="MV-2",
            note="Jones blew Phase Line Bird and gave no SITREP.",
            rating="NOGO",
            source="free_text",
        )
    ]
    context = build_reasoning_context(
        run_id="run-fallback",
        ingest=_ingest(),
        observations=observations,
        ocr_pages=[],
        kg_observation_refs={},
        extraction_uncertainties=[],
        errors=[],
    )

    def invalid_model_drafts(_context):
        raise ValueError("invalid model JSON")

    monkeypatch.setattr(provider, "_openai_recommendation_drafts", invalid_model_drafts)

    recommendations = asyncio.run(provider.draft_recommendations(observations, context))

    assert recommendations[0].intervention_id == "comm_degraded_sitrep"
    assert not recommendations[0].model_context_refs
    assert provider.diagnostics[-1].stage == "recommendation_ranking"
    assert provider.diagnostics[-1].provider == "library"
    assert provider.diagnostics[-1].status == "fallback"


def test_model_hallucinated_soldier_is_still_blocked_by_policy(monkeypatch) -> None:
    provider = ProviderClients(settings=Settings(openai_api_key="test-key"))
    observations = [
        Observation(
            soldier_id="Jones",
            task_code="MV-2",
            note="Jones blew Phase Line Bird and gave no SITREP.",
            rating="NOGO",
            source="free_text",
        )
    ]
    context = build_reasoning_context(
        run_id="run-hallucinated",
        ingest=_ingest(),
        observations=observations,
        ocr_pages=[],
        kg_observation_refs={},
        extraction_uncertainties=[],
        errors=[],
    )

    def model_drafts(_context):
        return [
            ModelRecommendationDraft(
                rank=1,
                intervention_id="comm_degraded_sitrep",
                target_soldier_id="Taylor",
                confidence=0.6,
                evidence_summary="The model incorrectly targeted a soldier not in context.",
                why_now="This draft should be caught by deterministic policy.",
                expected_learning_signal="The signal is irrelevant because the target is invalid.",
                risk_controls="The policy gate must block the hallucinated target.",
            )
        ]

    monkeypatch.setattr(provider, "_openai_recommendation_drafts", model_drafts)

    recommendations = asyncio.run(provider.draft_recommendations(observations, context))
    decision = PolicyEngine(roster=observations_to_roster(observations)).evaluate(
        recommendations[0]
    )

    assert recommendations[0].target_soldier_id == "Taylor"
    assert not decision.allowed
    assert "target soldier is not in the roster" in decision.reasons


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY is not configured")
def test_live_openai_extraction_smoke() -> None:
    provider = ProviderClients(settings=Settings(openai_api_key=os.getenv("OPENAI_API_KEY")))

    extracted = asyncio.run(
        provider.extract_observations_with_uncertainty(
            "Jones blew Phase Line Bird and did not send a SITREP."
        )
    )

    assert extracted.observations
    assert extracted.observations[0].soldier_id


def _ingest() -> IngestEnvelope:
    return IngestEnvelope(
        instructor_id="ri-1",
        platoon_id="plt-1",
        mission_id="m-1",
        phase=Phase.mountain,
        geo=GeoPoint(lat=35.0, lon=-83.0, grid_mgrs="17S"),
        free_text="Jones blew Phase Line Bird.",
    )
